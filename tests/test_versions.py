import gzip
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from nixkeeper_versions import (
    cli,
    cran,
    digest,
    emacs,
    releases,
    repology,
    stackage,
    sweep,
    typst,
)


def nix(attr, version, status, **extra):
    return {
        "repo": "nix_unstable",
        "srcname": attr,
        "version": version,
        "status": status,
        **extra,
    }


def other(repo, version, status="newest"):
    return {"repo": repo, "srcname": "x", "version": version, "status": status}


class FakeRepology:
    """Repology's API over projects ({name: entries}): pages of PAGE_SIZE in
    name order from an inclusive start, outdated ones only when asked."""

    def __init__(self, projects):
        self.projects = projects
        self.asked = []

    def page(self, start, outdated=False):
        self.asked.append(("page", start, outdated))
        names = sorted(
            n
            for n, entries in self.projects.items()
            if n >= start and (not outdated or digest.nix_outdated(entries))
        )
        return {n: self.projects[n] for n in names[: sweep.PAGE_SIZE]}

    def project(self, name):
        self.asked.append(("project", name))
        return self.projects.get(name)


class Walk(unittest.TestCase):
    def test_pages_from_inclusive_starts(self):
        names = [f"p{i:03}" for i in range(450)]
        fake = FakeRepology({n: [nix(n, "1", "newest")] for n in names})
        with mock.patch.object(repology, "page", side_effect=fake.page):
            found, end = sweep.walk()
            self.assertEqual((sorted(found), end), (names, None))
            found, end = sweep.walk("", max_pages=2)
        # Each page starts at the last name of the one before.
        self.assertEqual(end, names[398])
        self.assertEqual(len(found), 399)
        self.assertEqual(fake.asked[-1], ("page", names[199], False))


class Digest(unittest.TestCase):
    def test_trimmed_once_each(self):
        entries = [
            {**nix("xournalpp", "1.3.7", "outdated"), "summary": "notes"},
            {**other("arch", "1.3.8"), "binname": "xournalpp"},
            {**other("arch", "1.3.8"), "binname": "xournalpp-doc"},
        ]
        self.assertEqual(
            digest.trimmed(entries),
            [nix("xournalpp", "1.3.7", "outdated"), other("arch", "1.3.8")],
        )

    def test_put_drops_a_project_without_nixpkgs(self):
        found = digest.Digest({"a": [nix("a", "1", "newest")]}, {"a": "2026-10-01"})
        found.put("a", [other("arch", "2")], "2026-10-04")
        self.assertEqual((found.projects, found.checked), ({}, {}))

    def test_write_read(self):
        found = digest.Digest()
        found.put("a", [nix("a", "1", "outdated", vulnerable=True)], "2026-10-04")
        with tempfile.TemporaryDirectory() as d:
            digest.write(d, found, {"projects": 1})
            again, meta = digest.read(d)
            none, none_meta = digest.read(d + "/none")
        self.assertEqual((none.projects, none.checked, none_meta), ({}, {}, {}))
        self.assertEqual(again.projects, found.projects)
        self.assertEqual(again.checked, {"a": "2026-10-04"})
        self.assertEqual(meta, {"format": digest.FORMAT, "projects": 1})

    def test_the_first_digests_file_is_read_then_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            with open(f"{d}/meta.json", "w") as f:
                json.dump({"format": 1}, f)
            body = {
                "projects": {"a": [nix("a", "1", "newest")]},
                "checked": {"a": "2026-10-04"},
            }
            with gzip.open(f"{d}/projects.json.gz", "wt") as f:
                json.dump(body, f)
            found, _ = digest.read(d)
            self.assertEqual(found.projects, body["projects"])
            digest.write(d, found, {})
            self.assertEqual(sorted(os.listdir(d)), ["meta.json", "projects.jsonl.gz"])
            self.assertEqual(digest.read(d)[0].checked, {"a": "2026-10-04"})


class Main(unittest.TestCase):
    def run_main(self, d, projects, *args):
        fake = FakeRepology(projects)
        with (
            mock.patch.object(repology, "page", side_effect=fake.page),
            mock.patch.object(repology, "project", side_effect=fake.project),
            mock.patch.object(cli, "MIN_OUTDATED", 1),
            mock.patch.object(cli, "MIN_PROJECTS", 1),
            mock.patch.object(typst, "fetch", side_effect=OSError("offline")),
            mock.patch.object(emacs, "read_archives", side_effect=OSError("offline")),
            mock.patch.object(
                stackage, "read_stackage", side_effect=OSError("offline")
            ),
            mock.patch.object(releases, "update", return_value=None),
            mock.patch.object(cran, "read_indexes", side_effect=OSError("offline")),
            mock.patch("nixkeeper_versions.sets.update", return_value=None),
            mock.patch("nixkeeper_versions.registries.update", return_value=None),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            code = cli.main([d, *args])
        found, meta = digest.read(d)
        return code, found, meta, fake.asked

    def test_sources_only_leaves_repology_alone(self):
        day = {"a": [nix("a", "1", "outdated"), other("arch", "2")]}
        with tempfile.TemporaryDirectory() as d:
            self.run_main(d, day)
            with open(os.path.join(d, digest.PROJECTS), "rb") as f:
                projects = f.read()
            with mock.patch.object(
                typst,
                "update",
                return_value={"at": "2026-10-08T04:10:00+00:00", "packages": 1655},
            ):
                code, _, meta, asked = self.run_main(d, day, "--sources-only")
            self.assertEqual((code, asked), (0, []))  # nothing asked of Repology
            with open(os.path.join(d, digest.PROJECTS), "rb") as f:
                self.assertEqual(f.read(), projects)
        # The other sources' news in meta.json, Repology's as it was.
        self.assertEqual(meta["typst"]["packages"], 1655)
        self.assertEqual(meta["outdated"], 1)

    def test_two_days(self):
        day1 = {
            "a": [nix("a", "1", "newest"), other("arch", "1")],
            "b": [nix("b", "1", "outdated"), other("arch", "2")],
            "c": [nix("c", "1", "legacy"), nix("c2", "2", "newest")],
        }
        with tempfile.TemporaryDirectory() as d:
            code, found, meta, asked = self.run_main(d, day1)
            self.assertEqual(code, 0)
            self.assertEqual(sorted(found.projects), ["a", "b", "c"])
            self.assertEqual(meta["outdated"], 1)
            self.assertEqual(meta["rotation"]["next"], "")  # a whole lap
            self.assertIn("lapAt", meta["rotation"])

            # b was updated in nixpkgs, c left it, d is new and outdated.
            day2 = {
                "a": day1["a"],
                "b": [nix("b", "2", "newest"), other("arch", "2")],
                "d": [nix("d", "1", "outdated"), other("arch", "3")],
            }
            code, found, meta, asked = self.run_main(d, day2)
        self.assertEqual(code, 0)
        self.assertIn(("project", "b"), asked)  # no longer outdated: read again
        self.assertEqual(found.projects["b"][0]["status"], "newest")
        self.assertEqual(sorted(found.projects), ["a", "b", "d"])  # c is gone
        self.assertEqual((meta["caughtUp"], meta["caughtUpRead"]), (1, 1))

    def test_if_older_runs_once(self):
        day = {"a": [nix("a", "1", "outdated"), other("arch", "2")]}
        with tempfile.TemporaryDirectory() as d:
            code, _, first, _ = self.run_main(d, day, "--if-older", "12")
            self.assertEqual(code, 0)  # no digest yet: a run
            code, _, again, asked = self.run_main(d, day, "--if-older", "12")
            self.assertEqual((code, asked), (0, []))  # just ran: nothing read
            self.assertEqual(again, first)
            code, _, _, asked = self.run_main(d, day, "--if-older", "0")
            self.assertNotEqual(asked, [])

    def test_too_few_outdated_writes_nothing(self):
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.object(cli, "MIN_OUTDATED", 5),
        ):
            fake = FakeRepology({"b": [nix("b", "1", "outdated")]})
            with (
                mock.patch.object(repology, "page", side_effect=fake.page),
                mock.patch.object(typst, "fetch", side_effect=OSError("offline")),
                mock.patch.object(
                    emacs, "read_archives", side_effect=OSError("offline")
                ),
                mock.patch.object(
                    stackage, "read_stackage", side_effect=OSError("offline")
                ),
                mock.patch.object(releases, "update", return_value=None),
                mock.patch.object(cran, "read_indexes", side_effect=OSError("offline")),
                mock.patch("nixkeeper_versions.sets.update", return_value=None),
                mock.patch("nixkeeper_versions.registries.update", return_value=None),
                mock.patch("sys.stderr", io.StringIO()),
            ):
                self.assertEqual(cli.main([d]), 1)
            self.assertEqual(digest.read(d)[1], {})


class Moved(unittest.TestCase):
    def test_found_by_attribute_once_each(self):
        found = digest.Digest({"a": [nix("a", "1", "newest")]}, {"a": "2026-10-08"})
        answers = {
            "urlencode": ("urlencode-dead10ck", [nix("urlencode", "1.0.1", "newest")]),
            # One project for both attributes: asked once.
            "foo": (
                "foo-project",
                [nix("foo", "2", "newest"), nix("foo2", "2", "newest")],
            ),
            "gone": (None, None),  # removed from nixpkgs too
        }
        asked = []

        def lookup(attr):
            asked.append(attr)
            return answers[attr]

        with (
            mock.patch.object(repology, "project_for_attr", side_effect=lookup),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            moved, n = cli.find_moved(
                found, {"urlencode", "foo", "foo2", "gone", "a"}, "2026-10-09"
            )
        self.assertEqual(
            moved, {"urlencode": "urlencode-dead10ck", "foo": "foo-project"}
        )
        self.assertEqual(asked, ["foo", "gone", "urlencode"])  # not a, nor foo2
        self.assertEqual(n, 3)
        self.assertEqual(found.checked["urlencode-dead10ck"], "2026-10-09")

    def test_a_failure_stops_the_step_not_the_run(self):
        found = digest.Digest()
        with (
            mock.patch.object(
                repology, "project_for_attr", side_effect=OSError("down")
            ),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            self.assertEqual(cli.find_moved(found, {"a", "b"}, "2026-10-09"), ({}, 1))

    def test_main_looks_for_what_disappeared(self):
        # Outdated yesterday; today Repology has no such project (renamed).
        day1 = {
            "urlencode": [nix("urlencode", "1.0.1", "outdated"), other("arch", "1.6.0")]
        }
        with tempfile.TemporaryDirectory() as d:
            Main.run_main(self, d, day1)
            with mock.patch.object(
                repology,
                "project_for_attr",
                return_value=(
                    "urlencode-dead10ck",
                    [nix("urlencode", "1.0.1", "newest")],
                ),
            ) as lookup:
                code, found, meta, _ = Main.run_main(
                    self, d, {"other": [nix("o", "1", "outdated"), other("arch", "2")]}
                )
        self.assertEqual(code, 0)
        lookup.assert_called_once_with("urlencode")
        self.assertIn("urlencode-dead10ck", found.projects)
        self.assertEqual(meta["moved"], {"asked": 1, "found": 1})


class Get(unittest.TestCase):
    def response(self, body):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = json.dumps(body).encode()
        resp.__enter__.return_value.headers = {}
        return resp

    def error(self, code, retry_after=None):
        headers = {"Retry-After": retry_after} if retry_after else {}
        return urllib.error.HTTPError("u", code, "x", headers, io.BytesIO())

    def test_retries_as_asked_and_404(self):
        sleeps = []
        with (
            mock.patch(
                "urllib.request.urlopen",
                side_effect=[
                    self.error(429, "30"),
                    self.response({"a": []}),
                    self.error(404),
                ],
            ),
            mock.patch("time.sleep", side_effect=sleeps.append),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            self.assertEqual(repology.get("/x"), {"a": []})
            self.assertIsNone(repology.get("/y"))
        self.assertIn(30, sleeps)  # Repology's Retry-After, over the 10 s planned

    def test_project_for_attr_follows_the_redirect(self):
        resp = self.response([nix("urlencode", "1.0.1", "newest")])
        resp.__enter__.return_value.geturl.return_value = (
            "https://repology.org/api/v1/project/urlencode-dead10ck"
        )
        with mock.patch("urllib.request.urlopen", return_value=resp) as urlopen:
            name, entries = repology.project_for_attr("urlencode")
        self.assertEqual(name, "urlencode-dead10ck")
        self.assertEqual(entries[0]["srcname"], "urlencode")
        self.assertIn("name_type=srcname", urlopen.call_args.args[0].full_url)
        with (
            mock.patch("urllib.request.urlopen", side_effect=self.error(404)),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            self.assertEqual(repology.project_for_attr("gone"), (None, None))

    def test_user_agent_and_gzip(self):
        with mock.patch(
            "urllib.request.urlopen", return_value=self.response({})
        ) as urlopen:
            repology.page("tracy", outdated=True)
        req = urlopen.call_args.args[0]
        self.assertEqual(
            req.full_url,
            "https://repology.org/api/v1/projects/tracy/?inrepo=nix_unstable&outdated=1",
        )
        self.assertIn(
            "github.com/nixkeeper/digest-versions", req.get_header("User-agent")
        )
        self.assertEqual(req.get_header("Accept-encoding"), "gzip")


class Deadline(unittest.TestCase):
    """A whole answer has DEADLINE to arrive, however steadily it trickles
    in: a real server on this machine sending a byte at a time."""

    BODY = json.dumps({"x": "y" * 50}).encode()

    def setUp(self):
        body = self.BODY

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    for i in range(len(body)):
                        self.wfile.write(body[i : i + 1])
                        self.wfile.flush()
                        if self.path.startswith("/slow"):
                            time.sleep(0.02)
                except OSError:
                    pass  # the client gave up

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        for patcher in (
            mock.patch.object(
                repology, "API", f"http://127.0.0.1:{server.server_address[1]}"
            ),
            mock.patch.object(repology, "RETRY_DELAYS", ()),
            mock.patch.object(repology, "DEADLINE", 0.3),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_trickle_is_given_up(self):
        # 61 bytes at 20 ms each: 1.2 s, against 0.3 s.
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            repology.get("/slow")
        self.assertLess(time.monotonic() - started, 1)

    def test_a_steady_answer_arrives(self):
        self.assertEqual(repology.get("/fast"), json.loads(self.BODY))
