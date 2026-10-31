"""The newest versions of the gems and crates nixpkgs fetches from their
registry, which Repology mostly can't compare (rubyPackages' gems sit in
projects with nixpkgs alone; many crates too), written to
data/registries.json.gz:

    {"format": 1, "fetchedAt": "2026-10-31T04:10:00+00:00",
     "registries": {"rubygems": {"fetchedAt": "...",
                                 "attrs": {"rubyPackages.rails": "rails", ...},
                                 "packages": {"rails": {"version": "8.1.4"}, ...}},
                    "crates.io": {"fetchedAt": "...",
                                  "attrs": {"_0x": "ohx", ...},
                                  "packages": {"ohx": {"version": "0.1.0"},
                                               "pijul": {"version": "0.15.0",
                                                         "preRelease": "1.0.0-beta.24"},
                                               ...}}}}

- rubygems: each gem's newest release (RubyGems' versions/<gem>/latest.json,
  no pre-release), for the attributes whose source is a gem
  (rubygems.org/gems/<gem>-<version>.gem: rubyPackages, rubyPackages_3_4,
  rubyPackages_4_0 and a few applications);
- crates.io: each crate's newest version that isn't yanked or a
  pre-release (unless it has only those), and its newest pre-release when
  that's newer ("preRelease": for a package nixpkgs takes pre-releases of,
  as pijul's 1.0.0 betas), from crates.io's sparse index (a small file per
  crate, served from a CDN), for the attributes fetched from crates.io
  (static.crates.io/crates/<crate>/<version>/download).

"attrs" say which gem or crate each attribute is: the crate's name is often
not the attribute's (_0x is ohx). The names come from the sources in
nixpkgs.json.gz (packages.py), as the last run published them. One request
per gem or crate, REGISTRY_PAUSE apart. A registry that can't be read, or
whose answers fail for over MAX_FAILED of its names, keeps its last part."""

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

FILE = "registries.json.gz"
FORMAT = 1
GEM = "https://rubygems.org/api/v1/versions/{name}/latest.json"
CRATE = "https://index.crates.io/{path}"
# A source fetched from each registry: the gem or crate it names.
GEM_SOURCE = re.compile(r"^https://rubygems\.org/(?:gems|downloads)/(.+)\.gem$")
CRATE_SOURCE = re.compile(
    r"^https://(?:static\.crates\.io/crates|crates\.io/api/v1/crates)"
    r"/([^/]+)/[^/]+/download$"
)
# A pause between two requests to a registry (RubyGems allows 10 a second;
# crates.io's index is a CDN), and the share of names that may fail (a gem
# removed from RubyGems) before the registry's last part is kept instead.
REGISTRY_PAUSE = 0.25
MAX_FAILED = 0.1


def gem_of(pkg):
    """The gem an attribute's source is (nixpkgs.json.gz's entry), or None.
    The version is cut from the file name: gem names have dashes too."""
    m = GEM_SOURCE.match(((pkg.get("src") or {}).get("url")) or "")
    version = pkg.get("version") or ""
    if not m or not version or not m.group(1).endswith(f"-{version}"):
        return None
    return m.group(1)[: -len(version) - 1]


def crate_of(pkg):
    """The crate an attribute's source is (nixpkgs.json.gz's entry), or
    None."""
    m = CRATE_SOURCE.match(((pkg.get("src") or {}).get("url")) or "")
    return m and m.group(1)


def crate_path(name):
    """A crate's file in crates.io's sparse index (Cargo's layout)."""
    name = name.lower()
    if len(name) <= 2:
        return f"{len(name)}/{name}"
    if len(name) == 3:
        return f"3/{name[0]}/{name}"
    return f"{name[:2]}/{name[2:4]}/{name}"


def gem(body):
    """{"version"} from RubyGems' latest.json answer, or None without one."""
    version = json.loads(body).get("version")
    return {"version": version} if version else None


def crate(body):
    """{"version", "preRelease"?} from a crate's sparse index file (a JSON
    line per version), or None when every version is yanked."""
    versions = [json.loads(line) for line in body.decode().splitlines() if line]
    kept = [v["vers"] for v in versions if not v.get("yanked")]
    if not kept:
        return None
    released = [v for v in kept if "-" not in v.split("+")[0]]
    newest = max(kept, key=version_key)
    if not released:
        return {"version": newest}
    version = max(released, key=version_key)
    return {"version": version, **({"preRelease": newest} if newest != version else {})}


# Each registry: which attributes it has, where each name's answer is, and
# how to read it.
REGISTRIES = {
    "rubygems": (gem_of, lambda name: GEM.format(name=name), gem),
    "crates.io": (crate_of, lambda name: CRATE.format(path=crate_path(name)), crate),
}


def read_registry(registry, nixpkgs, get=download.get):
    """(attrs, packages) for a registry: {attr: name} for nixpkgs'
    attributes fetched from it, {name: {"version"}} for the names it
    answered. Raises ValueError when over MAX_FAILED of them fail."""
    name_of, url_of, parse = REGISTRIES[registry]
    attrs = {}
    for attr, pkg in sorted((nixpkgs or {}).items()):
        if name := name_of(pkg):
            attrs[attr] = name
    names = sorted(set(attrs.values()))
    found, failed = {}, []
    for i, name in enumerate(names):
        if i:
            time.sleep(REGISTRY_PAUSE)
        try:
            entry = parse(get(url_of(name)))
        except (urllib.error.URLError, OSError, ValueError) as e:
            failed.append(f"{name} ({e})")
            continue
        if entry:
            found[name] = entry
    if failed:
        print(
            f"  {registry}: {len(failed):,} not read: {', '.join(failed[:5])}",
            file=sys.stderr,
        )
    if not names or len(failed) > MAX_FAILED * len(names):
        raise ValueError(f"{len(failed):,} of {len(names):,} names not read")
    return attrs, found


def read(directory):
    """registries.json.gz in directory, or None if there's none."""
    try:
        with gzip.open(os.path.join(directory, FILE), "rt") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def write(directory, registries, now):
    """Write registries.json.gz (registries: {registry: {"fetchedAt",
    "attrs", "packages"}}) to directory."""
    os.makedirs(directory, exist_ok=True)
    body = {
        "format": FORMAT,
        "fetchedAt": now.isoformat(timespec="seconds"),
        "registries": registries,
    }
    data = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    with open(os.path.join(directory, FILE), "wb") as f:
        f.write(gzip.compress(data, compresslevel=9, mtime=0))


def update(directory, now: datetime, get=download.get):
    """Bring registries.json.gz up to date: {"at", "registries": {registry:
    packages}} for meta.json, or None when no registry could be read (the
    last file stays, as it does without nixpkgs.json.gz). A registry that
    can't be read keeps its last part, with a warning."""
    nixpkgs = (packages.read(directory) or {}).get("packages")
    if not nixpkgs:
        print("::warning::No nixpkgs.json.gz: registries not read", file=sys.stderr)
        return None
    registries = dict((read(directory) or {}).get("registries") or {})
    read_now = {}
    for registry in REGISTRIES:
        print(f"Reading {registry}...", file=sys.stderr)
        started = time.monotonic()
        try:
            attrs, found = read_registry(registry, nixpkgs, get)
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"::warning::{registry}: {e}; kept the last", file=sys.stderr)
            continue
        print(
            f"  {len(found):,} of {len(set(attrs.values())):,} names, for "
            f"{len(attrs):,} attributes, in {time.monotonic() - started:.0f} s",
            file=sys.stderr,
        )
        registries[registry] = {
            "fetchedAt": now.isoformat(timespec="seconds"),
            "attrs": attrs,
            "packages": found,
        }
        read_now[registry] = len(found)
    if not read_now:
        return None
    write(directory, dict(sorted(registries.items())), now)
    return {"at": now.isoformat(timespec="seconds"), "registries": read_now}
