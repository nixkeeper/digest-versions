"""nixpkgs' own facts about its packages, for readers without Nix: every
attribute's pname and version, from the channel's package index (daily),
and where its source comes from, from the weekly evaluation releases.py
already does (src: the repository, tag, rev and first URL). For
nixkeeper-vulnerabilities, which matches packages to advisories by registry
name and version (OSV's ecosystems) and by source repository and tag or
commit (OSV's GIT), and has no Nix nor a Brotli reader for the index.

data/nixpkgs.json.gz:

    {"format": 1, "indexedAt": "...", "revision": "...",
     "evaluatedAt": "...", "evaluatedRevision": "...",
     "packages": {
       "python313Packages.requests": {"pname": "requests", "version": "2.32.5"},
       "ripgrep": {"pname": "ripgrep", "version": "15.1.0",
                   "src": {"gitRepoUrl": "https://github.com/BurntSushi/ripgrep.git",
                           "tag": "15.1.0", "url": "https://github.com/..."}}}}

Every attribute of the index (nested sets included); "src" for those the
weekly evaluation covers (outside the sets nixkeeper compares with their
own sources, and in the few of them whose sources the digest reads:
releases.evaluates), with only the fields it has. A failed
read keeps the last file."""

import gzip
import json
import os
import sys
import urllib.error
from datetime import datetime, timedelta

from nixkeeper.sources import nixpkgs

FILE = "nixpkgs.json.gz"
FORMAT = 1
# The index is read again when the file's is older than this: daily runs
# read it each time, a second run the same day doesn't.
INDEX_EVERY = timedelta(hours=20)
# Fewer attributes than this means the index was cut short: not written.
MIN_PACKAGES = 100_000
SRC_FIELDS = ("gitRepoUrl", "tag", "rev", "url")


def read(directory):
    """nixpkgs.json.gz in directory, or None if there's none."""
    try:
        with gzip.open(os.path.join(directory, FILE), "rt") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def write(directory, body):
    os.makedirs(directory, exist_ok=True)
    data = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    with open(os.path.join(directory, FILE), "wb") as f:
        f.write(gzip.compress(data, compresslevel=9, mtime=0))


def from_index(index):
    """{attr: {"pname", "version"}} of the package index, without empty
    fields."""
    return {
        attr: {k: p[k] for k in ("pname", "version") if p.get(k)}
        for attr, p in sorted(index.items())
    }


def src_of(found):
    """The fields of a source (nixpkgs.sources') that it has."""
    return {k: found[k] for k in SRC_FIELDS if found.get(k)}


def merge(packages, sources):
    """packages with each one's src from sources ({attr: src}), where it has
    one."""
    out = {}
    for attr, p in packages.items():
        src = sources.get(attr)
        out[attr] = {**p, "src": src} if src else p
    return out


def update(directory, now, evaluated=None, load=None, revision=None):
    """Bring nixpkgs.json.gz up to date: the index when the file's is older
    than INDEX_EVERY; evaluated, (revision, {attr: src}) when releases.py
    evaluated sources this run, replaces the sources (else the last file's
    stay). Returns {"indexedAt", "evaluatedAt", "packages", "sources"} for
    meta.json, or None when there's nothing (no index yet, and none read)."""
    load = load or nixpkgs.load_index
    revision = revision or nixpkgs.channel_revision
    last = read(directory) or {}
    body = {k: v for k, v in last.items() if k != "packages"}
    packages = {
        attr: {k: v for k, v in p.items() if k != "src"}
        for attr, p in (last.get("packages") or {}).items()
    }
    sources = {
        attr: p["src"] for attr, p in (last.get("packages") or {}).items() if "src" in p
    }
    indexed = last.get("indexedAt")
    if not indexed or now - datetime.fromisoformat(indexed) >= INDEX_EVERY:
        print("Reading nixpkgs' package index...", file=sys.stderr)
        try:
            at = revision()
            fresh = from_index(load())
            if len(fresh) < MIN_PACKAGES:
                raise ValueError(
                    f"only {len(fresh):,} attributes (expected over {MIN_PACKAGES:,})"
                )
            packages = fresh
            body.update(indexedAt=now.isoformat(timespec="seconds"), revision=at)
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"::warning::nixpkgs index: not read ({e})", file=sys.stderr)
    if evaluated:
        at, found = evaluated
        sources = {a: s for a, f in found.items() if (s := src_of(f))}
        body.update(evaluatedAt=now.isoformat(timespec="seconds"), evaluatedRevision=at)
    if not packages:
        return None
    merged = merge(packages, sources)
    write(directory, {**body, "format": FORMAT, "packages": merged})
    with_src = sum(1 for p in merged.values() if "src" in p)
    print(
        f"  nixpkgs: {len(merged):,} attributes, {with_src:,} with their source",
        file=sys.stderr,
    )
    return {
        "indexedAt": body.get("indexedAt"),
        "evaluatedAt": body.get("evaluatedAt"),
        "packages": len(merged),
        "sources": with_src,
    }
