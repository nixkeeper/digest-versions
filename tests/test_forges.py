import io
import json
import threading
import unittest
from unittest import mock

from nixkeeper_versions import forges

TODAY = "2026-10-28"


class Forges(unittest.TestCase):
    def setUp(self):
        for patcher in (
            mock.patch("sys.stderr", io.StringIO()),
            mock.patch.object(forges, "pace"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def serve(self, rest, batches=None):
        """forges' requests answered: REST by URL (a body, None for a 404, or
        an exception), GraphQL by host ({host: [nodes]}); returns the URLs
        asked."""
        asked = []

        def get(url, accept=None, **_):
            asked.append(url)
            answer = rest[url]
            if isinstance(answer, Exception):
                raise answer
            return None if answer is None else json.dumps(answer)

        def graphql(host, query):
            asked.append(f"graphql {host}")
            return {"projects": {"nodes": (batches or {}).get(host, [])}}

        for patcher in (
            mock.patch.object(forges.http, "get", side_effect=get),
            mock.patch.object(forges, "graphql", side_effect=graphql),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        return asked

    def test_gitlab_batch_then_tags(self):
        api = "https://gitlab.com/api/v4/projects/expliot_framework%2Fexpliot"
        tags = f"{api}/repository/tags?order_by=version&sort=desc&per_page=100"
        asked = self.serve(
            {tags: [{"name": "0.11.1"}, {"name": "0.11.0"}]},
            {
                "gitlab.com": [
                    {
                        "fullPath": "expliot_framework/expliot",
                        "archived": True,
                        "releases": {
                            "nodes": [{"tagName": "0.11.1", "upcomingRelease": False}]
                        },
                    }
                ]
            },
        )
        found = forges.read(["gitlab:gitlab.com/expliot_framework/expliot|"], TODAY)
        self.assertEqual(
            found,
            {
                "gitlab:gitlab.com/expliot_framework/expliot|": {
                    "tags": ["0.11.1", "0.11.0"],
                    "release": "0.11.1",
                    "archived": True,
                    "read": TODAY,
                }
            },
        )
        self.assertEqual(asked, ["graphql gitlab.com", tags])  # no project request

    def test_gitlab_renamed_or_gone(self):
        # The batch leaves an old path out: REST redirects it to the project.
        old = "https://gitlab.com/api/v4/projects/warsaw%2Fpublic"
        gone = "https://gitlab.com/api/v4/projects/o%2Fgone"
        self.serve(
            {
                old: {"path_with_namespace": "flufl/public"},
                f"{old}/repository/tags?order_by=version&sort=desc&per_page=100": [
                    {"name": "5.1"}
                ],
                gone: None,
            }
        )
        found = forges.read(
            ["gitlab:gitlab.com/warsaw/public|", "gitlab:gitlab.com/o/gone|"], TODAY
        )
        self.assertEqual(
            found["gitlab:gitlab.com/warsaw/public|"],
            {"tags": ["5.1"], "name": "gitlab.com/flufl/public", "read": TODAY},
        )
        self.assertIsNone(found["gitlab:gitlab.com/o/gone|"])

    def test_gitlab_without_graphql(self):
        # gitlab.gnome.org refuses GraphQL without a browser: REST only.
        api = "https://gitlab.gnome.org/api/v4/projects/GNOME%2Flibgsf"
        asked = self.serve(
            {
                api: {"path_with_namespace": "GNOME/libgsf"},
                f"{api}/repository/tags?order_by=version&sort=desc&per_page=100": [
                    {"name": "LIBGSF_1_14_60"}
                ],
            }
        )
        found = forges.read(["gitlab:gitlab.gnome.org/GNOME/libgsf|LIBGSF_"], TODAY)
        self.assertEqual(
            found["gitlab:gitlab.gnome.org/GNOME/libgsf|LIBGSF_"],
            {"tags": ["LIBGSF_1_14_60"], "read": TODAY},
        )
        self.assertNotIn("graphql gitlab.gnome.org", asked)

    def test_forgejo(self):
        api = "https://codeberg.org/api/v1/repos/gaiasky/gaiasky"
        self.serve(
            {
                api: {"full_name": "gaiasky/gaiasky", "archived": False},
                f"{api}/tags?limit=100": [{"name": "3.9.2"}, {"name": "3.9.1"}],
                f"{api}/releases/latest": {"tag_name": "3.9.2"},
                "https://codeberg.org/api/v1/repos/old/name": {
                    "full_name": "new/name",
                    "archived": True,
                },
                "https://codeberg.org/api/v1/repos/old/name/tags?limit=100": [],
                "https://codeberg.org/api/v1/repos/old/name/releases/latest": None,
            }
        )
        found = forges.read(
            ["forgejo:codeberg.org/gaiasky/gaiasky|", "forgejo:codeberg.org/old/name|"],
            TODAY,
        )
        self.assertEqual(
            found["forgejo:codeberg.org/gaiasky/gaiasky|"],
            {"tags": ["3.9.2", "3.9.1"], "release": "3.9.2", "read": TODAY},
        )
        self.assertEqual(
            found["forgejo:codeberg.org/old/name|"],
            {
                "tags": [],
                "archived": True,
                "name": "codeberg.org/new/name",
                "read": TODAY,
            },
        )

    def test_a_site_that_stops_answering_waits(self):
        keys = [f"forgejo:codeberg.org/o/r{i}|" for i in range(5)]
        rest = {
            f"https://codeberg.org/api/v1/repos/o/r{i}": OSError("down")
            for i in range(5)
        }
        asked = self.serve(rest)
        self.assertEqual(forges.read(keys, TODAY), {})  # none read: not gone
        self.assertEqual(len(asked), forges.MAX_FAILURES_IN_A_ROW)


class Pace(unittest.TestCase):
    def test_a_site_waits_for_itself_not_for_another(self):
        slept = []
        clock = iter([0.0, 0.0, 0.2, 0.2, 0.3, 0.3])
        with (
            mock.patch.object(
                forges.time, "monotonic", side_effect=lambda: next(clock)
            ),
            mock.patch.object(forges.time, "sleep", side_effect=slept.append),
            mock.patch.dict(forges._last_asked, clear=True),
        ):
            forges.pace("https://gitlab.com/a")  # first: no wait
            forges.pace("https://codeberg.org/a")  # another site: no wait
            forges.pace("https://gitlab.com/b")  # 0.3 s after its last
        self.assertEqual(len(slept), 1)
        self.assertAlmostEqual(
            slept[0], forges.nixkeeper_config.SITE_PAUSE_SECONDS - 0.3
        )

    def test_sites_side_by_side(self):
        # Two sites that each take a while: read at the same time.
        started = []
        both = threading.Barrier(2, timeout=5)

        def reader(host, keys, today):
            started.append(host)
            both.wait()  # only returns once both sites are being read
            return {k: None for k in keys}

        with (
            mock.patch("sys.stderr", io.StringIO()),
            mock.patch.object(forges, "gitlab", side_effect=reader),
            mock.patch.object(forges, "forgejo", side_effect=reader),
        ):
            found = forges.read(
                ["gitlab:gitlab.com/a/b|", "forgejo:codeberg.org/c/d|"], TODAY
            )
        self.assertEqual(sorted(started), ["codeberg.org", "gitlab.com"])
        self.assertEqual(len(found), 2)


if __name__ == "__main__":
    unittest.main()
