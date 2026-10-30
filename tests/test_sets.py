import gzip
import io
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from nixkeeper_versions import sets

NOW = datetime(2026, 10, 30, 4, 10, tzinfo=UTC)

# Trimmed from CHICKEN 5's egg index (2026-10-30): a version per line, in no
# particular order.
EGGS = """"2"
(miscmacros "1.0" 5162 "206c008a9d4f221991a7ee158ac2343e70b3ba42" () ())
(svnwiki2html "0.1.3" 2039 "e25e3a6c814bc380d76a8db2b8bab87b737128e4" (qwiki) ())
(svnwiki2html "0.1.10" 2037 "f2a649ef69b2d024e25dd572d08d5f632dfc35a0" (qwiki) ())
(svnwiki2html "0.0.2" 1857 "b6f6819957596271a13eab288c3f3b05022e0628" (srfi-1) ())
"""

# Trimmed from Akku's index (2026-10-30): a name as a string or as a
# library name's parts, each with its versions.
AKKU = """#!r6rs ; -*- mode: scheme; coding: utf-8 -*-
(import (akku format index))

(package
  (name (chibi crypto md5))
  (versions
    ((version "0.9.0") (synopsis "MD5"))
    ((version "0.10.0") (synopsis "MD5"))))

(package
  (name "ac-d-bus")
  (versions
    ((version "1.0.0-beta.0") (synopsis "D-Bus"))
    ((version "1.0.0") (synopsis "D-Bus"))))
"""

# One extension of extensions.gnome.org's query (2026-10-30).
EXTENSION = {
    "uuid": "color-picker@tuberry",
    "link": "/extension/3396/color-picker/",
    "shell_version_map": {
        "3.36": {"pk": 1, "version": 12},
        "48": {"pk": 2, "version": 46},
        "49": {"pk": 3, "version": 47},
        "50": {"pk": 4, "version": 49},
        "50.1": {"pk": 5, "version": 48},
        "51": {"pk": 6, "version": 50},
    },
}


def az_version(version, cli="2.61.0", **more):
    return {
        "downloadUrl": f"https://example.org/x-{version}.whl",
        "sha256Digest": "00",
        "metadata": {
            "name": "x",
            "version": version,
            "azext.minCliCoreVersion": cli,
            "summary": "X",
            **more,
        },
    }


class Indexes(unittest.TestCase):
    def test_chicken_eggs_newest_by_number(self):
        self.assertEqual(
            sets.chicken(EGGS),
            {
                "miscmacros": {"version": "1.0"},
                # 0.1.10 after 0.1.3: by number, not as text.
                "svnwiki2html": {"version": "0.1.10"},
            },
        )

    def test_a_gzipped_index_served_as_it_is(self):
        self.assertEqual(sets.text(gzip.compress(b"egg")), "egg")
        self.assertEqual(sets.text(b"egg"), "egg")

    def test_akku_names_as_nixpkgs_has_them(self):
        self.assertEqual(
            sets.akku(AKKU),
            {
                # A release after its beta.
                "ac-d-bus": {"version": "1.0.0"},
                "chibi-crypto-md5": {"version": "0.10.0"},
            },
        )

    def test_gnome_extension_newest_per_shell(self):
        self.assertEqual(
            sets.gnome_extension(EXTENSION),
            (
                "color-picker@tuberry",
                {
                    "slug": "color-picker",
                    # GNOME 3's left out; 50.1 counts as 50, its newest kept.
                    "shells": {"48": "46", "49": "47", "50": "49", "51": "50"},
                },
            ),
        )
        self.assertIsNone(sets.gnome_extension({"link": "/extension/1/x/"}))

    def test_gnome_page_by_page(self):
        pages = {
            1: {"numpages": 2, "extensions": [EXTENSION]},
            2: {"numpages": 2, "extensions": [{**EXTENSION, "uuid": "b@c"}]},
        }
        asked = []

        def get(url):
            asked.append(url)
            return json.dumps(pages[int(url.rsplit("=", 1)[1])]).encode()

        with mock.patch.object(sets.time, "sleep") as slept:
            found = sets.gnome(get)
        self.assertEqual(sorted(found), ["b@c", "color-picker@tuberry"])
        self.assertEqual(len(asked), 2)
        self.assertEqual(slept.call_count, 1)  # between pages, not after

    def test_gnome_attrs_by_their_sources_uuid(self):
        url = "https://extensions.gnome.org/extension-data/{}.v49.shell-extension.zip"
        nixpkgs = {
            "gnomeExtensions.color-picker": {
                "src": {"url": url.format("color-pickertuberry")}
            },
            # Its slug is another extension's: only its source tells.
            "gnomeExtensions.applications-menu": {
                "src": {
                    "url": url.format(
                        "apps-menugnome-shell-extensions.gcampax.github.com"
                    )
                }
            },
            "gnomeExtensions.gsconnect": {
                "src": {"url": "https://github.com/x/y.tar.gz"}
            },
            "hello": {"src": {"url": url.format("color-pickertuberry")}},
        }
        apps_menu = "apps-menu@gnome-shell-extensions.gcampax.github.com"
        extensions = {
            "color-picker@tuberry": {},
            apps_menu: {},
            "Applications_Menu@rmy.pobox.com": {},
        }
        self.assertEqual(
            sets.gnome_attrs(extensions, nixpkgs),
            {
                "gnomeExtensions.applications-menu": apps_menu,
                "gnomeExtensions.color-picker": "color-picker@tuberry",
            },
        )
        self.assertEqual(sets.gnome_attrs(extensions, None), {})

    def test_azure_versions_nixpkgs_would_consider(self):
        index = {
            "extensions": {
                "x": [
                    az_version("1.0.0b1"),
                    az_version("1.0.0", cli="2.70.0"),
                    {**az_version("1.1.0"), "sha256Digest": None},  # no hash
                ],
                "y": [{"metadata": {"name": "y"}}],  # no version
            }
        }
        self.assertEqual(
            sets.azure(index),
            {"x": {"versions": [["1.0.0b1", "2.61.0"], ["1.0.0", "2.70.0"]]}},
        )


class Update(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("sys.stderr", io.StringIO())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_each_set_kept_when_its_index_fails(self):
        many = {f"p{i}": {"version": "1.0"} for i in range(5)}
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.dict(sets.MIN_PACKAGES, {"a": 3, "b": 3}, clear=True),
        ):
            meta = sets.update(d, NOW, {"a": lambda: many, "b": lambda: many})
            self.assertEqual(meta, {"at": NOW.isoformat(), "sets": {"a": 5, "b": 5}})

            def down():
                raise OSError("down")

            later = NOW + timedelta(hours=12)
            meta = sets.update(d, later, {"a": down, "b": lambda: {"p": {}}})
            self.assertIsNone(meta)  # neither read: the last file stays
            meta = sets.update(d, later, {"a": down, "b": lambda: many})
            found = sets.read(d)["sets"]
        self.assertEqual(meta["sets"], {"b": 5})
        self.assertEqual(found["a"]["fetchedAt"], NOW.isoformat())
        self.assertEqual(found["b"]["fetchedAt"], later.isoformat())
        self.assertEqual(found["a"]["packages"], many)


if __name__ == "__main__":
    unittest.main()
