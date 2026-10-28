import gzip
import io
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from nixkeeper_versions import releases

NOW = datetime(2026, 10, 8, 4, 10, tzinfo=UTC)
# As nixkeeper.inferred.github_check works them out (2026-10-07's proof).
CHECKS = {
    "whisky": {
        "repo": "Whisky-App/Whisky",
        "tags": r"^v([0-9]+(?:[.][0-9]+)+)$",
        "version": "2.3.5",
    },
    "python313Packages.azure-search-documents": {
        "repo": "Azure/azure-sdk-for-python",
        "tags": r"^azure\-search\-documents_([0-9]+(?:[.][0-9]+)+)$",
        "version": "11.5.2",
    },
    "gone": {
        "repo": "someone/gone",
        "tags": r"^([0-9]+(?:[.][0-9]+)+)$",
        "version": "1.0",
    },
}


class Keys(unittest.TestCase):
    def test_prefix_and_key(self):
        self.assertEqual(releases.prefix(CHECKS["whisky"]["tags"]), "v")
        self.assertEqual(
            releases.key(CHECKS["python313Packages.azure-search-documents"]),
            "Azure/azure-sdk-for-python|azure-search-documents_",
        )
        self.assertEqual(releases.key(CHECKS["gone"]), "someone/gone|")

    def test_query_filters_monorepo_tags(self):
        q = releases.query(
            [
                "Whisky-App/Whisky|v",
                "Azure/azure-sdk-for-python|azure-search-documents_",
            ]
        )
        self.assertIn('r0: repository(owner: "Whisky-App", name: "Whisky")', q)
        self.assertIn('query: "azure-search-documents_"', q)
        self.assertNotIn('query: "v"', q)  # a one-letter prefix filters nothing
        self.assertIn("rateLimit { remaining }", q)

    def test_sets_are_left_to_their_own_sources(self):
        self.assertTrue(releases.in_bulk_set("haskellPackages.aeson"))
        self.assertTrue(releases.in_bulk_set("darwin.foo"))
        self.assertFalse(releases.in_bulk_set("python313Packages.requests"))
        self.assertFalse(releases.in_bulk_set("whisky"))


class Newest(unittest.TestCase):
    def test_the_newest_matching_tag_and_the_release(self):
        repo = {
            "tags": ["v2.4.0-rc1", "v2.4.0", "nightly", "v2.3.5"],
            "release": "v2.3.5",
        }
        self.assertEqual(
            releases.newest(CHECKS["whisky"], repo),
            {"tag": "2.4.0", "release": "2.3.5"},
        )
        # No release, or one that doesn't match the scheme: tags only.
        self.assertEqual(
            releases.newest(CHECKS["whisky"], {"tags": ["v1.0"]}), {"tag": "1.0"}
        )
        self.assertEqual(
            releases.newest(CHECKS["whisky"], {"tags": [], "release": "nightly"}), {}
        )


class Due(unittest.TestCase):
    def test_all_never_read_then_outdated_then_the_longest_unread(self):
        keys = {f"r{i}|" for i in range(6)}
        repos = {k: {"read": "2026-10-10"} for k in keys if k != "r5|"}
        # r1 and r3 weren't reached by the last run, cut short.
        repos["r1|"] = repos["r3|"] = {"read": "2026-10-09"}
        self.assertEqual(
            releases.due(keys, repos, {"r4|"}),
            # r5 never read, r4 outdated, then the oldest read, by name.
            ["r5|", "r4|", "r1|", "r3|", "r0|", "r2|"],
        )


class Update(unittest.TestCase):
    def setUp(self):
        for patcher in (
            mock.patch("sys.stderr", io.StringIO()),
            mock.patch.object(releases.time, "sleep"),
            mock.patch.object(releases, "MIN_CHECKS", 1),
            mock.patch.dict(os.environ, {"GITHUB_TOKEN": "token"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_update(self, d, answers, now=NOW, outdated=(), checks=CHECKS):
        def graphql(token, query, variables):
            self.assertEqual(token, "token")
            data = {"rateLimit": {"remaining": 900}}
            for i in range(len(answers)):
                if f"r{i}:" in query:
                    data[f"r{i}"] = answers[i]
            return data

        with (
            mock.patch.object(releases.nixpkgs, "channel_revision", return_value="abc"),
            mock.patch.object(
                releases.nixpkgs, "load_index", return_value={a: {} for a in CHECKS}
            ),
            mock.patch.object(releases, "evaluate", return_value=checks) as evaluated,
            mock.patch.object(releases.github, "graphql", side_effect=graphql),
        ):
            meta = releases.update(d, now, outdated)
        return meta, evaluated.called

    def test_evaluated_read_and_written(self):
        answers = [
            # Sorted keys: Azure..., Whisky..., someone/gone.
            {
                # Only cased differently: the same repository.
                "nameWithOwner": "azure/azure-sdk-for-python",
                "refs": {"nodes": [{"name": "azure-search-documents_11.6.0"}]},
            },
            {
                "nameWithOwner": "Whisky-App/Whisky",
                "refs": {"nodes": [{"name": "v2.4.0"}]},
                "latestRelease": {"tagName": "v2.4.0"},
            },
            None,  # gone
        ]
        with tempfile.TemporaryDirectory() as d:
            meta, evaluated = self.run_update(d, answers)
            self.assertTrue(evaluated)
            self.assertEqual((meta["packages"], meta["read"]), (3, 3))
            with gzip.open(os.path.join(d, releases.FILE), "rt") as f:
                found = json.load(f)
        self.assertEqual(
            found["packages"]["whisky"],
            {
                "repo": "Whisky-App/Whisky",
                "version": "2.3.5",
                "tag": "2.4.0",
                "release": "2.4.0",
                "read": "2026-10-08",
            },
        )
        self.assertEqual(
            found["packages"]["python313Packages.azure-search-documents"]["tag"],
            "11.6.0",
        )
        self.assertNotIn(
            "movedTo", found["packages"]["python313Packages.azure-search-documents"]
        )
        # Not found once: not gone yet.
        self.assertNotIn("gone", found["packages"]["gone"])
        self.assertEqual(found["gone"], [])
        self.assertEqual(
            (found["revision"], found["evaluatedAt"]), ("abc", NOW.isoformat())
        )

    def test_gone_when_not_found_twice_in_a_row(self):
        answers = [{"refs": {"nodes": []}}, {"refs": {"nodes": []}}, None]
        later = NOW + timedelta(hours=12)
        with tempfile.TemporaryDirectory() as d:
            self.run_update(d, answers)
            self.run_update(d, answers, later)
            with gzip.open(os.path.join(d, releases.FILE), "rt") as f:
                found = json.load(f)
        self.assertEqual(
            found["packages"]["gone"],
            {
                "repo": "someone/gone",
                "version": "1.0",
                "gone": True,
                "read": "2026-10-08",
            },
        )
        self.assertEqual(found["gone"], ["someone/gone"])

    def test_a_query_github_fails_makes_nothing_gone(self):
        # 2026-10-27: "Something went wrong while executing your query", and
        # every repository of that query null, or left out.
        tagged = {"refs": {"nodes": [{"name": "v2.4.0"}]}}
        later = NOW + timedelta(hours=12)
        with tempfile.TemporaryDirectory() as d:
            self.run_update(d, [tagged, tagged, tagged])
            self.run_update(d, [None, None, None], later)
            with gzip.open(os.path.join(d, releases.FILE), "rt") as f:
                found = json.load(f)
            self.assertEqual(found["gone"], [])
            self.assertEqual(found["packages"]["whisky"]["tag"], "2.4.0")
            with mock.patch.object(releases.github, "graphql", return_value={}):
                releases.update(d, later + timedelta(hours=12))
            with gzip.open(os.path.join(d, releases.FILE), "rt") as f:
                found = json.load(f)
        self.assertEqual(found["gone"], [])

    def test_archived_and_moved(self):
        answers = [
            {"nameWithOwner": "Azure/azure-sdk-for-python", "isArchived": True},
            # GitHub answers for the old name with where it redirects.
            {"nameWithOwner": "Whisky-Org/Whisky", "refs": {"nodes": []}},
            {"nameWithOwner": "someone/gone", "isArchived": False},
        ]
        with tempfile.TemporaryDirectory() as d:
            self.run_update(d, answers)
            with gzip.open(os.path.join(d, releases.FILE), "rt") as f:
                packages = json.load(f)["packages"]
        azure = packages["python313Packages.azure-search-documents"]
        self.assertEqual((azure.get("archived"), azure.get("movedTo")), (True, None))
        whisky = packages["whisky"]
        self.assertEqual(
            (whisky.get("archived"), whisky.get("movedTo")), (None, "Whisky-Org/Whisky")
        )
        self.assertNotIn("archived", packages["gone"])
        self.assertNotIn("gone", packages["gone"])

    def test_query_asks_name_and_archived(self):
        q = releases.query(["Whisky-App/Whisky|v"])
        self.assertIn("nameWithOwner isArchived", q)

    def test_evaluated_weekly(self):
        answers = [{"refs": {"nodes": []}}] * 3
        with tempfile.TemporaryDirectory() as d:
            self.run_update(d, answers)
            _, evaluated = self.run_update(d, answers, NOW + timedelta(days=1))
            self.assertFalse(evaluated)
            _, evaluated = self.run_update(d, answers, NOW + timedelta(days=7))
            self.assertTrue(evaluated)

    def test_no_token_nothing_read(self):
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.dict(os.environ, clear=True),
        ):
            meta, _ = self.run_update(d, [])
            self.assertIsNone(meta)
            self.assertIsNone(releases.read(d))

    def test_github_down_keeps_what_it_had(self):
        with tempfile.TemporaryDirectory() as d:
            self.run_update(d, [{"refs": {"nodes": [{"name": "v2.4.0"}]}}] * 3)
            with (
                mock.patch.object(
                    releases.github, "graphql", side_effect=OSError("down")
                ),
                mock.patch.object(releases, "evaluate", return_value=CHECKS),
            ):
                meta = releases.update(d, NOW + timedelta(days=1))
            self.assertEqual(meta["read"], 0)
            self.assertEqual(
                releases.read(d)["packages"]["whisky"]["read"], "2026-10-08"
            )

    def test_other_forges_written_apart_and_gone_after_two_runs(self):
        # Packages from GitLab instances and Codeberg: read through
        # forges.py, written apart from GitHub's ("elsewhere").
        lab = {
            "forge": "gitlab",
            "repo": "gitlab.com/expliot_framework/expliot",
            "tags": r"^([0-9]+(?:[.][0-9]+)+)$",
            "version": "0.11.0",
        }
        berg = {
            "forge": "forgejo",
            "repo": "codeberg.org/o/gone",
            "tags": r"^([0-9]+(?:[.][0-9]+)+)$",
            "version": "1.0",
        }
        checks = {**CHECKS, "expliot": lab, "gone-berg": berg}
        answers = {
            "gitlab:gitlab.com/expliot_framework/expliot|": {
                "tags": ["0.11.1"],
                "archived": True,
                "read": "2026-10-08",
            },
            "forgejo:codeberg.org/o/gone|": None,
        }
        github = [{"refs": {"nodes": []}}] * 3
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.object(releases.forges, "read", return_value=answers),
        ):
            self.run_update(d, github, checks=checks)
            first = releases.read(d)
            self.run_update(d, github, NOW + timedelta(hours=12), checks=checks)
            second = releases.read(d)
        self.assertEqual(
            first["elsewhere"]["expliot"],
            {
                "forge": "gitlab",
                "repo": "gitlab.com/expliot_framework/expliot",
                "version": "0.11.0",
                "tag": "0.11.1",
                "archived": True,
                "read": "2026-10-08",
            },
        )
        self.assertNotIn("expliot", first["packages"])  # GitHub's only
        self.assertNotIn("gone", first["elsewhere"]["gone-berg"])  # not found once
        self.assertTrue(second["elsewhere"]["gone-berg"]["gone"])  # twice
        self.assertNotIn("codeberg.org/o/gone", second["gone"])  # GitHub's list

    def test_a_stale_release_elsewhere_is_left_out(self):
        # KDE's tellico, 2026-10-28: its latest release v3.5.5, its tags v4.3.
        kde = {
            "forge": "gitlab",
            "repo": "invent.kde.org/office/tellico",
            "tags": r"^v([0-9]+(?:[.][0-9]+)+)$",
            "version": "4.2.2",
        }
        answers = {
            "gitlab:invent.kde.org/office/tellico|v": {
                "tags": ["v4.3", "v4.2.2"],
                "release": "v3.5.5",
                "read": "2026-10-08",
            }
        }
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.object(releases.forges, "read", return_value=answers),
        ):
            self.run_update(
                d, [{"refs": {"nodes": []}}] * 3, checks={**CHECKS, "tellico": kde}
            )
            entry = releases.read(d)["elsewhere"]["tellico"]
        self.assertEqual((entry["tag"], entry.get("release")), ("4.3", None))

    def test_state_from_before_evaluated_again(self):
        # Checks made before other forges counted: evaluated at once.
        with tempfile.TemporaryDirectory() as d:
            releases.write_json(
                d,
                releases.STATE,
                {"format": 1, "checks": CHECKS, "evaluatedAt": NOW.isoformat()},
            )
            _, evaluated = self.run_update(d, [{"refs": {"nodes": []}}] * 3)
        self.assertTrue(evaluated)


if __name__ == "__main__":
    unittest.main()
