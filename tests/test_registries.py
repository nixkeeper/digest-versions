import io
import json
import tempfile
import unittest
import urllib.error
from datetime import UTC, datetime, timedelta
from unittest import mock

from nixkeeper_versions import packages, registries

NOW = datetime(2026, 10, 31, 4, 10, tzinfo=UTC)


def src(url, version):
    return {"src": {"url": url}, "version": version}


# As nixpkgs.json.gz has them (2026-10-31).
NIXPKGS = {
    "rubyPackages.rails": src("https://rubygems.org/gems/rails-8.1.3.gem", "8.1.3"),
    "rubyPackages_3_4.rails": src("https://rubygems.org/gems/rails-8.1.3.gem", "8.1.3"),
    # A dash in the gem's name.
    "rubyPackages.sass-embedded": src(
        "https://rubygems.org/gems/sass-embedded-1.107.1.gem", "1.107.1"
    ),
    # The attribute isn't the crate's name.
    "_0x": src("https://static.crates.io/crates/ohx/0.1.0/download", "0.1.0"),
    "aasvg-rs": src(
        "https://crates.io/api/v1/crates/aasvg-cli/1.0.0/download", "1.0.0"
    ),
    "hello": src("https://ftp.gnu.org/gnu/hello/hello-2.12.2.tar.gz", "2.12.2"),
}


def index_file(*versions):
    return "\n".join(
        json.dumps({"name": "x", "vers": v, "yanked": y}) for v, y in versions
    ).encode()


class Names(unittest.TestCase):
    def test_gem_and_crate_from_the_source(self):
        self.assertEqual(registries.gem_of(NIXPKGS["rubyPackages.rails"]), "rails")
        self.assertEqual(
            registries.gem_of(NIXPKGS["rubyPackages.sass-embedded"]), "sass-embedded"
        )
        self.assertIsNone(registries.gem_of(NIXPKGS["hello"]))
        # A file name that doesn't end in the version: not guessed.
        self.assertIsNone(
            registries.gem_of(src("https://rubygems.org/gems/rails-8.1.3.gem", "8"))
        )
        self.assertEqual(registries.crate_of(NIXPKGS["_0x"]), "ohx")
        self.assertEqual(registries.crate_of(NIXPKGS["aasvg-rs"]), "aasvg-cli")
        self.assertIsNone(registries.crate_of(NIXPKGS["hello"]))

    def test_crate_paths_as_cargo_lays_them_out(self):
        self.assertEqual(registries.crate_path("a"), "1/a")
        self.assertEqual(registries.crate_path("ab"), "2/ab")
        self.assertEqual(registries.crate_path("ohx"), "3/o/ohx")
        self.assertEqual(registries.crate_path("Aasvg-cli"), "aa/sv/aasvg-cli")


class Answers(unittest.TestCase):
    def test_crate_newest_release_not_yanked(self):
        body = index_file(
            ("0.9.0", False), ("0.10.0", False), ("0.11.0", True), ("1.0.0-rc.1", False)
        )
        # The newest pre-release beside it, for a package nixpkgs takes them of.
        self.assertEqual(
            registries.crate(body), {"version": "0.10.0", "preRelease": "1.0.0-rc.1"}
        )
        self.assertEqual(
            registries.crate(index_file(("1.0.0-rc.1", False), ("1.0.0", False))),
            {"version": "1.0.0"},
        )
        # Only pre-releases: the newest of them.
        body = index_file(("1.0.0-alpha.1", False), ("1.0.0-alpha.2", False))
        self.assertEqual(registries.crate(body), {"version": "1.0.0-alpha.2"})
        self.assertIsNone(registries.crate(index_file(("1.0.0", True))))

    def test_gem_latest(self):
        self.assertEqual(registries.gem(b'{"version":"8.1.4"}'), {"version": "8.1.4"})
        self.assertIsNone(registries.gem(b"{}"))


class Update(unittest.TestCase):
    def setUp(self):
        for patcher in (
            mock.patch("sys.stderr", io.StringIO()),
            mock.patch.object(registries.time, "sleep"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def serve(self, answers):
        def get(url):
            if url not in answers:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return answers[url]

        return get

    def test_both_registries_read(self):
        get = self.serve(
            {
                registries.GEM.format(name="rails"): b'{"version":"8.1.4"}',
                registries.GEM.format(name="sass-embedded"): b'{"version":"1.107.1"}',
                "https://index.crates.io/3/o/ohx": index_file(("0.1.0", False)),
                "https://index.crates.io/aa/sv/aasvg-cli": index_file(
                    ("1.0.0", False), ("1.1.0", False)
                ),
            }
        )
        with tempfile.TemporaryDirectory() as d:
            packages.write(d, {"packages": NIXPKGS})
            meta = registries.update(d, NOW, get)
            found = registries.read(d)["registries"]
        self.assertEqual(
            meta,
            {"at": NOW.isoformat(), "registries": {"rubygems": 2, "crates.io": 2}},
        )
        self.assertEqual(
            found["rubygems"]["attrs"],
            {
                "rubyPackages.rails": "rails",
                "rubyPackages.sass-embedded": "sass-embedded",
                "rubyPackages_3_4.rails": "rails",
            },
        )
        self.assertEqual(
            found["crates.io"]["attrs"], {"_0x": "ohx", "aasvg-rs": "aasvg-cli"}
        )
        self.assertEqual(
            found["crates.io"]["packages"]["aasvg-cli"], {"version": "1.1.0"}
        )

    def test_a_registry_kept_when_too_many_fail(self):
        everything = {
            registries.GEM.format(name="rails"): b'{"version":"8.1.4"}',
            registries.GEM.format(name="sass-embedded"): b'{"version":"1.107.1"}',
            "https://index.crates.io/3/o/ohx": index_file(("0.1.0", False)),
            "https://index.crates.io/aa/sv/aasvg-cli": index_file(("1.0.0", False)),
        }
        with tempfile.TemporaryDirectory() as d:
            packages.write(d, {"packages": NIXPKGS})
            registries.update(d, NOW, self.serve(everything))
            later = NOW + timedelta(hours=12)
            # crates.io down: its part stays as it was, RubyGems' is new.
            gems_only = {k: v for k, v in everything.items() if "rubygems" in k}
            meta = registries.update(d, later, self.serve(gems_only))
            found = registries.read(d)["registries"]
            self.assertEqual(meta["registries"], {"rubygems": 2})
            self.assertEqual(found["crates.io"]["fetchedAt"], NOW.isoformat())
            self.assertEqual(found["rubygems"]["fetchedAt"], later.isoformat())
            # Nothing read: the file stays.
            self.assertIsNone(registries.update(d, later, self.serve({})))

    def test_nothing_without_nixpkgs(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(registries.update(d, NOW, self.serve({})))
            self.assertIsNone(registries.read(d))


if __name__ == "__main__":
    unittest.main()
