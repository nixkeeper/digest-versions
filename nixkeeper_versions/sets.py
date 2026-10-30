"""The indexes nixpkgs' generated sets are made from, which Repology mostly
can't compare (it sees nixpkgs alone): each set's newest versions, read as
nixpkgs' own update script reads them, written to data/sets.json.gz:

    {"format": 1, "fetchedAt": "2026-10-30T04:10:00+00:00",
     "sets": {"chickenPackages_5": {"fetchedAt": "...",
                                    "packages": {"7off": {"version": "1.33"}, ...}},
              "gnomeExtensions": {"fetchedAt": "...",
                                  "attrs": {"gnomeExtensions.move-clock":
                                            "move-clock@rmy.pobox.com", ...},
                                  "packages": {"move-clock@rmy.pobox.com":
                                               {"slug": "move-clock",
                                                "shells": {"48": "36", ...}}, ...}},
              "azure-cli-extensions": {"fetchedAt": "...",
                                       "packages": {"acat": {"versions":
                                                    [["1.0.0b1", "2.61.0"], ...]}}},
              ...}}

- chickenPackages_5 and _6: CHICKEN's egg index for that major version
  (egg-tarballs/<major>/index.gz, every version of every egg; nixpkgs'
  update.sh reads the same eggs from git), by egg name;
- akkuPackages: Akku's index (Akku-index.scm, every version of every
  package, as nixpkgs' update.sh reads it), by attribute name (a library
  name's parts joined with "-": (chibi crypto md5) is chibi-crypto-md5);
- gnomeExtensions: extensions.gnome.org's extension query, a page of 25 at
  a time (135 pages on 2026-10-30, one a second, as nixpkgs'
  update-extensions.py walks it), by UUID: its slug (nixpkgs' pname
  without "gnome-shell-extension-", unless renamed) and its newest version
  for each GNOME Shell major version from 40 on ("shells"), from which
  nixkeeper takes the newest for the shells nixpkgs merges into
  gnomeExtensions (its own major and the two before, and any newer: the
  numbers only grow). Its "attrs" say which extension each of nixpkgs'
  attributes is ({"gnomeExtensions.color-picker": "color-picker@tuberry"}),
  from the sources nixpkgs.json.gz has (packages.py), which name it by its
  UUID without "@" (extension-data/<uuid>.v<n>.shell-extension.zip): slugs
  aren't unique (106 of nixpkgs' extensions share theirs on 2026-10-30);
- azure-cli-extensions: the Azure CLI's extension index (index.json, as
  nixpkgs' extensions-tool.py reads it), by name: each version, with the
  oldest Azure CLI it runs on (azext.minCliCoreVersion), from which
  nixkeeper takes the newest one nixpkgs' azure-cli runs.

"version" in the first two is the newest by Repology's version order
(nixkeeper.versions). Each set has its own "fetchedAt": a set whose index
can't be read, or has too few packages, keeps its last part."""

import gzip
import json
import os
import re
import sys
import time
import urllib.error
from datetime import datetime

from nixkeeper.versions import version_key

from . import download, packages

FILE = "sets.json.gz"
FORMAT = 1
CHICKEN = "https://code.call-cc.org/egg-tarballs/{major}/index.gz"
AKKU = "https://archive.akkuscm.org/archive/Akku-index.scm"
GNOME = "https://extensions.gnome.org/extension-query/?n_per_page=25&page={page}"
AZURE = "https://azcliextensionsync.blob.core.windows.net/index1/index.json"
# A pause between extensions.gnome.org's pages, and the most to read (a
# site that never ends its pages isn't followed further).
GNOME_PAUSE = 1.0
GNOME_MAX_PAGES = 400
# An extension's download, in nixpkgs' sources: its UUID without "@".
GNOME_DOWNLOAD = re.compile(r"/extension-data/(.+)\.v[0-9]+\.shell-extension\.zip$")
# An egg index line: (name "version" size "sha1" (deps) (test deps)).
EGG = re.compile(r'^\(([^\s()"]+) "([^"]+)"')
# An Akku package: its name, a string or a library name's parts.
AKKU_PACKAGE = re.compile(r'^\(package\s*\(name (?:"([^"]+)"|\(([^)]*)\))\)', re.M)
AKKU_VERSION = re.compile(r'\(version "([^"]+)"\)')
# The fewest packages each set's index should have (about two-thirds of
# 2026-10-30's: 642 eggs for CHICKEN 5, 214 for 6, 431 Akku packages,
# 3,375 GNOME extensions, 228 Azure CLI extensions).
MIN_PACKAGES = {
    "chickenPackages_5": 400,
    "chickenPackages_6": 140,
    "akkuPackages": 280,
    "gnomeExtensions": 2200,
    "azure-cli-extensions": 150,
}


def newest(versions):
    """The newest of versions, by Repology's order."""
    return max(versions, key=version_key)


def chicken(text):
    """{egg: {"version"}} from an egg index's text: each egg's newest."""
    found = {}
    for line in text.splitlines():
        if m := EGG.match(line):
            found.setdefault(m.group(1), []).append(m.group(2))
    return {egg: {"version": newest(v)} for egg, v in sorted(found.items())}


def akku(text):
    """{name: {"version"}} from Akku's index: each package's newest, by its
    attribute name in nixpkgs."""
    found = {}
    starts = list(AKKU_PACKAGE.finditer(text))
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        name = m.group(1) or "-".join(m.group(2).split())
        if versions := AKKU_VERSION.findall(text, m.end(), end):
            found[name] = {"version": newest(versions)}
    return dict(sorted(found.items()))


def gnome_extension(entry):
    """(uuid, {"slug", "shells"}) for an extension of the query's answer:
    its newest version for each GNOME Shell major version from 40 on ("46"
    for 46.2; GNOME 3's are left out), or None without a UUID."""
    uuid = entry.get("uuid")
    if not uuid:
        return None
    shells = {}
    for shell, ext in (entry.get("shell_version_map") or {}).items():
        major = shell.split(".")[0]
        version = (ext or {}).get("version")
        if not major.isdigit() or int(major) < 40 or version is None:
            continue
        if version > shells.get(major, -1):
            shells[major] = version
    slug = (entry.get("link") or "").rstrip("/").rsplit("/", 1)[-1]
    return uuid, {
        "slug": slug,
        "shells": {k: str(v) for k, v in sorted(shells.items())},
    }


def gnome(get=download.get):
    """{uuid: {"slug", "shells"}} for every extension on extensions.gnome.org,
    a page at a time. Raises when a page can't be read."""
    found, page, pages = {}, 1, 1
    while page <= min(pages, GNOME_MAX_PAGES):
        answer = json.loads(get(GNOME.format(page=page)))
        pages = answer.get("numpages") or 0
        for entry in answer.get("extensions") or []:
            if extension := gnome_extension(entry):
                found[extension[0]] = extension[1]
        page += 1
        if page <= pages:
            time.sleep(GNOME_PAUSE)
    return dict(sorted(found.items()))


def gnome_attrs(extensions, nixpkgs):
    """{attr: uuid} for nixpkgs' gnomeExtensions whose source names one of
    extensions (nixpkgs: nixpkgs.json.gz's "packages", with their "src")."""
    plain = {uuid.replace("@", ""): uuid for uuid in extensions}
    found = {}
    for attr, pkg in (nixpkgs or {}).items():
        if not attr.startswith("gnomeExtensions."):
            continue
        m = GNOME_DOWNLOAD.search(((pkg.get("src") or {}).get("url")) or "")
        if m and m.group(1) in plain:
            found[attr] = plain[m.group(1)]
    return dict(sorted(found.items()))


def azure(index):
    """{name: {"versions": [[version, minCliCoreVersion], ...]}} from the
    Azure CLI's extension index: the versions nixpkgs' extensions-tool.py
    would consider (with a version, the oldest CLI it runs on, a summary,
    a download and its hash)."""
    found = {}
    for name, versions in (index.get("extensions") or {}).items():
        kept = []
        for v in versions:
            meta = v.get("metadata") or {}
            if not all(
                meta.get(k) for k in ("version", "azext.minCliCoreVersion", "summary")
            ) or not (v.get("downloadUrl") and v.get("sha256Digest")):
                continue
            kept.append([meta["version"], meta["azext.minCliCoreVersion"]])
        if kept:
            found[name] = {"versions": kept}
    return dict(sorted(found.items()))


def text(body):
    """body as text, ungzipped when it's a .gz file served as it is."""
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return body.decode()


# Each set: how to read its packages (raises when it can't).
READERS = {
    "chickenPackages_5": lambda: chicken(text(download.get(CHICKEN.format(major=5)))),
    "chickenPackages_6": lambda: chicken(text(download.get(CHICKEN.format(major=6)))),
    "akkuPackages": lambda: akku(text(download.get(AKKU))),
    "gnomeExtensions": gnome,
    "azure-cli-extensions": lambda: azure(json.loads(download.get(AZURE))),
}


def read(directory):
    """sets.json.gz in directory, or None if there's none."""
    try:
        with gzip.open(os.path.join(directory, FILE), "rt") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def write(directory, sets, now):
    """Write sets.json.gz (sets: {set: {"fetchedAt", "packages"}}) to
    directory."""
    os.makedirs(directory, exist_ok=True)
    body = {
        "format": FORMAT,
        "fetchedAt": now.isoformat(timespec="seconds"),
        "sets": sets,
    }
    data = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    with open(os.path.join(directory, FILE), "wb") as f:
        f.write(gzip.compress(data, compresslevel=9, mtime=0))


def update(directory, now: datetime, readers=None):
    """Bring sets.json.gz up to date: {"at", "sets": {set: packages}} for
    meta.json, or None when no set could be read (the last file stays).
    A set whose index can't be read, or has too few packages, keeps its
    last part, with a warning."""
    sets = dict((read(directory) or {}).get("sets") or {})
    read_now = {}
    for name, reader in (readers or READERS).items():
        print(f"Reading the {name} index...", file=sys.stderr)
        try:
            found = reader()
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"::warning::The {name} index: {e}; kept the last", file=sys.stderr)
            continue
        if len(found) < MIN_PACKAGES.get(name, 1):
            print(
                f"::warning::Only {len(found):,} packages in the {name} index "
                f"(expected over {MIN_PACKAGES[name]:,}): kept the last",
                file=sys.stderr,
            )
            continue
        sets[name] = {
            "fetchedAt": now.isoformat(timespec="seconds"),
            "packages": found,
        }
        if name == "gnomeExtensions":
            nixpkgs = (packages.read(directory) or {}).get("packages")
            sets[name]["attrs"] = gnome_attrs(found, nixpkgs)
        read_now[name] = len(found)
    if not read_now:
        return None
    write(directory, dict(sorted(sets.items())), now)
    return {"at": now.isoformat(timespec="seconds"), "sets": read_now}
