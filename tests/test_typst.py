import gzip
import io
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from unittest import mock

from nixkeeper_versions import cli, digest, repology, typst

NOW = datetime(2026, 10, 7, 4, 10, tzinfo=UTC)

# Trimmed from Typst Universe's index (2026-10-07): every version of every
# package, in no particular order.
INDEX = [
    {"name": "cetz", "version": "0.9.2", "updatedAt": 1759000000},
    {"name": "cetz", "version": "0.10.0", "updatedAt": 1759200000},
    {"name": "cetz", "version": "0.2.0", "updatedAt": 1700000000},
    {"name": "a2c-nums", "version": "0.0.1", "updatedAt": 1704708827},
    {"name": "broken", "version": ""},
]


class Newest(unittest.TestCase):
    def test_each_packages_newest_by_number(self):
        self.assertEqual(
            typst.newest(INDEX),
            {
                "a2c-nums": {"version": "0.0.1", "released": "2024-01-08"},
                # 0.10.0 after 0.9.2: by number, not as text.
                "cetz": {"version": "0.10.0", "released": "2025-09-30"},
            },
        )

    def test_no_day_when_the_index_has_none(self):
        self.assertEqual(
            typst.newest([{"name": "x", "version": "1.0.0"}]),
            {"x": {"version": "1.0.0"}},
        )


class Update(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("sys.stderr", io.StringIO())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_written_and_read(self):
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.object(typst, "fetch", return_value=INDEX),
            mock.patch.object(typst, "MIN_PACKAGES", 1),
        ):
            self.assertEqual(
                typst.update(d, NOW), {"at": "2026-10-07T04:10:00+00:00", "packages": 2}
            )
            found = typst.read(d)
        self.assertEqual(found["fetchedAt"], "2026-10-07T04:10:00+00:00")
        self.assertEqual(found["packages"]["cetz"]["version"], "0.10.0")

    def test_the_last_stays_when_the_index_fails_or_is_short(self):
        with tempfile.TemporaryDirectory() as d:
            with (
                mock.patch.object(typst, "fetch", return_value=INDEX),
                mock.patch.object(typst, "MIN_PACKAGES", 1),
            ):
                typst.update(d, NOW)
            before = typst.read(d)
            with mock.patch.object(typst, "fetch", side_effect=OSError("down")):
                self.assertIsNone(typst.update(d, NOW))
            with mock.patch.object(typst, "fetch", return_value=INDEX):
                self.assertIsNone(typst.update(d, NOW))  # 2 < MIN_PACKAGES
            self.assertEqual(typst.read(d), before)

    def test_fetch_asks_politely(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.headers = {"Content-Encoding": "gzip"}
        resp.read.return_value = gzip.compress(json.dumps(INDEX).encode())
        with mock.patch("urllib.request.urlopen", return_value=resp) as urlopen:
            self.assertEqual(typst.fetch(), INDEX)
        req = urlopen.call_args[0][0]
        self.assertEqual(req.get_header("User-agent"), repology.USER_AGENT)
        self.assertEqual(req.get_header("Accept-encoding"), "gzip")


class InTheRun(unittest.TestCase):
    def test_written_even_when_repology_fails(self):
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.object(typst, "fetch", return_value=INDEX),
            mock.patch.object(typst, "MIN_PACKAGES", 1),
            mock.patch.object(repology, "page", side_effect=OSError("down")),
            mock.patch("nixkeeper_versions.emacs.read_archives", side_effect=OSError),
            mock.patch(
                "nixkeeper_versions.stackage.read_stackage", side_effect=OSError
            ),
            mock.patch("nixkeeper_versions.releases.update", return_value=None),
            mock.patch("nixkeeper_versions.sets.update", return_value=None),
            mock.patch("nixkeeper_versions.registries.update", return_value=None),
            mock.patch("nixkeeper_versions.cran.read_indexes", side_effect=OSError),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            with self.assertRaises(OSError):
                cli.main([d])
            self.assertTrue(os.path.exists(os.path.join(d, typst.FILE)))
            self.assertEqual(digest.read(d)[1], {})  # no Repology digest


if __name__ == "__main__":
    unittest.main()
