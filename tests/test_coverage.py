import tempfile
import unittest

from nixkeeper_versions import coverage, packages, releases

GH = "https://github.com/a/b.git"
PACKAGES = {
    "emacsPackages.magit": {"pname": "magit"},
    "darwin.libiconv": {"pname": "libiconv"},
    "ripgrep": {"pname": "ripgrep", "src": {"gitRepoUrl": GH, "tag": "15.1.0"}},
    "coreaction": {"pname": "coreaction", "src": {"gitRepoUrl": GH, "tag": "v5"}},
    "hello-wrapper": {"pname": "hello-wrapper"},
    # Two attributes of one source, a third of another.
    "python313Packages.six": {"pname": "six", "src": {"url": "mirror://pypi/s/six"}},
    "python314Packages.six": {"pname": "six", "src": {"url": "mirror://pypi/s/six"}},
    "perl": {"pname": "perl", "src": {"url": "mirror://cpan/perl.tar.gz"}},
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
                },
            )
            self.assertEqual(
                coverage.count(d),
                {
                    "attributes": 8,
                    "sets": 2,
                    "forges": {"github": 1, "gitlab": 1},
                    "noSource": 1,
                    "repologyOnly": 3,
                    "repologyOnlySources": 2,
                },
            )

    def test_without_releases_or_nixpkgs(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(coverage.count(d))
            packages.write(d, {"format": 1, "packages": PACKAGES})
            self.assertEqual(coverage.count(d)["repologyOnly"], 5)
