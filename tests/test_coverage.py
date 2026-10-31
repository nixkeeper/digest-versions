import tempfile
import unittest
from datetime import UTC, datetime

from nixkeeper_versions import coverage, packages, registries, releases

NOW = datetime(2026, 10, 31, tzinfo=UTC)
GH = "https://github.com/a/b.git"
PACKAGES = {
    "emacsPackages.magit": {"pname": "magit"},
    "darwin.libiconv": {"pname": "libiconv"},
    "ripgrep": {"pname": "ripgrep", "src": {"gitRepoUrl": GH, "tag": "15.1.0"}},
    "coreaction": {"pname": "coreaction", "src": {"gitRepoUrl": GH, "tag": "v5"}},
    "hello-wrapper": {"pname": "hello-wrapper"},
    "abduco": {"pname": "abduco", "src": {"gitRepoUrl": GH, "rev": "a" * 40}},
    # Two attributes of one source, a third of another.
    "python313Packages.six": {"pname": "six", "src": {"url": "mirror://pypi/s/six"}},
    "python314Packages.six": {"pname": "six", "src": {"url": "mirror://pypi/s/six"}},
    "perl": {"pname": "perl", "src": {"url": "mirror://cpan/perl.tar.gz"}},
    # A crate crates.io has, and one it doesn't.
    "_0x": {
        "pname": "0x",
        "src": {"url": "https://static.crates.io/crates/ohx/0.1.0/download"},
    },
    "gone": {
        "pname": "gone",
        "src": {"url": "https://static.crates.io/crates/gone/1/download"},
    },
}


class Coverage(unittest.TestCase):
    def test_counts(self):
        with tempfile.TemporaryDirectory() as d:
            packages.write(d, {"format": 1, "packages": PACKAGES})
            releases.write_json(
                d,
                releases.FILE,
                {
                    "packages": {"ripgrep": {"repo": "a/b"}},
                    "elsewhere": {"coreaction": {"forge": "gitlab"}},
                    "pins": {"abduco": {"repo": "a/b"}},
                },
            )
            registries.write(
                d,
                {
                    "crates.io": {
                        "attrs": {"_0x": "ohx", "gone": "gone"},
                        "packages": {"ohx": {"version": "0.1.0"}},
                    }
                },
                NOW,
            )
            self.assertEqual(
                coverage.count(d),
                {
                    "attributes": 11,
                    "sets": 2,
                    "forges": {"github": 1, "gitlab": 1},
                    "pins": 1,
                    "registries": {"crates.io": 1},
                    "noSource": 1,
                    "repologyOnly": 4,
                    "repologyOnlySources": 3,
                },
            )

    def test_without_releases_or_nixpkgs(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(coverage.count(d))
            packages.write(d, {"format": 1, "packages": PACKAGES})
            self.assertEqual(coverage.count(d)["repologyOnly"], 8)
