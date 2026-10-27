"""GitHub releases: the newest release and tag of each nixpkgs package
fetched from a GitHub tag, from GitHub itself, so nixkeeper's verdict on
those packages doesn't depend on Repology (docs/design/github-releases.md
in nixkeeper). About 23,000 packages outside the sets updated in bulk.

Two parts, with nixkeeper's own code (its flake input):

1. Weekly, where each package comes from: its src, evaluated at the
   channel's revision (nixkeeper.sources.nixpkgs.sources, a couple of
   minutes for every package), and from it the tag check nixkeeper's
   update checks would work out (nixkeeper.inferred.github_check): the
   repository, and the tag scheme as a pattern. Needs Nix.
2. Each run, every repository, so a release shows within 12 hours: those
   never read first, then those of packages Repology calls outdated, then
   the rest, the longest unread first (a run GitHub cuts short, its token
   low on points, leaves the newest read for the next). One GraphQL query
   per BATCH repositories, each one's newest tags by commit date and its
   latest release (drafts and pre-releases left out): about 860 queries
   and points on 2026-10-11, an hour (62 minutes); with
   NIXKEEPER_VERSIONS_TOKEN (a personal token's 5,000 points an hour),
   else the workflow's own (1,000: enough, barely).

Kept between runs in releases-state.json.gz (where each package comes
from, what each repository had); written for nixkeeper as
data/releases.json.gz:

    {"format": 1, "fetchedAt": "...", "evaluatedAt": "...", "revision": "...",
     "packages": {"whisky": {"repo": "Whisky-App/Whisky", "version": "2.3.5",
                             "tag": "2.4.0", "release": "2.4.0",
                             "read": "2026-10-08"}, ...},
     "gone": ["owner/repo", ...]}

each package's version in nixpkgs, the newest version among its
repository's tags that match its scheme ("tag"), and its latest release's
when that matches ("release"; what nixkeeper prefers when there is one: a
tag alone may not be released yet), with when the repository was read.
What became of the repository, when something did: "archived": true
(read-only, its owner's way of saying it's no longer worked on),
"movedTo": its name now, when GitHub answers for nixpkgs' name under
another (renamed, or moved to another owner; GitHub redirects the old
name), or "gone": true, with no tag or release, when GitHub doesn't know
it any more (deleted, or made private), two runs in a row. Gone
repositories are also listed in "gone". A run that fails keeps the last
files."""

import gzip
import json
import os
import re
import sys
import time
import urllib.error
from datetime import datetime, timedelta

from nixkeeper import config as nixkeeper_config
from nixkeeper import inferred
from nixkeeper.sources import github, nixpkgs
from nixkeeper.versions import version_key

FILE = "releases.json.gz"
STATE = "releases-state.json.gz"
FORMAT = 1
# The evaluation of where packages come from: weekly, a chunk of attributes
# per nix eval (memory stays small), and so many checks at the least (about
# 23,000 on 2026-10-07): fewer, something went wrong.
EVALUATE_EVERY = timedelta(days=7)
CHUNK = 5000
MIN_CHECKS = 10000
# Repositories per query (100 made GitHub give up, 502: sorting tags by date
# is heavy; 25 cost a point each), its newest tags to look through, and a
# pause between queries.
BATCH = 25
TAGS = 30
PAUSE = 1.0
# Stop when the token has fewer points than this left, or after so many
# queries failing in a row (GitHub isn't answering).
MIN_POINTS = 50
MAX_FAILURES_IN_A_ROW = 3


def in_bulk_set(attr):
    """Whether attr is in a set nixkeeper compares with the set's own source
    (rPackages, ...) or doesn't version (darwin, ...): not checked here."""
    first = attr.split(".", 1)[0] if "." in attr else None
    return first in nixkeeper_config.SET_PROFILES or first in (
        nixkeeper_config.UNVERSIONED_SETS
    )


def evaluate(revision, attrs, sources=None):
    """{attr: {"repo", "tags", "version"}} for the attrs fetched from a GitHub
    tag, with the pattern their tags must match (inferred.github_check): src
    evaluated at revision, CHUNK attributes at a time; every attr's src goes
    into sources too, when given (packages.py publishes them). Raises
    nixpkgs.EvalError when nixpkgs doesn't evaluate."""
    found = {}
    attrs = sorted(attrs)
    for start in range(0, len(attrs), CHUNK):
        part = attrs[start : start + CHUNK]
        print(
            f"  evaluating {start + len(part):,} of {len(attrs):,}...", file=sys.stderr
        )
        for attr, src in nixpkgs.sources(part, revision).items():
            if sources is not None:
                sources[attr] = src
            check, _ = inferred.github_check(src, attr)
            if check:
                found[attr] = {
                    "repo": check["github"],
                    "tags": check["tags"],
                    "version": src.get("version") or "",
                }
    return found


def prefix(pattern):
    """The literal start of a tag pattern ("^v(...)$": "v"; monorepos' are
    longer: "azure-search-documents_"), or ""."""
    head = pattern.removeprefix("^").split("(", 1)[0]
    return re.sub(r"\\(.)", r"\1", head)


def key(check):
    """The repository and tag prefix a check reads: "owner/repo|prefix"."""
    return f"{check['repo']}|{prefix(check['tags'])}"


def query(keys):
    """One GraphQL query for keys' repositories: each one's newest TAGS tags
    by commit date (only those containing its prefix, for monorepos), and
    its latest release."""
    fields = []
    for i, k in enumerate(keys):
        repo, start = k.split("|", 1)
        owner, name = repo.split("/", 1)
        filtered = f", query: {json.dumps(start)}" if len(start) > 1 else ""
        fields.append(
            f"r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{"
            " nameWithOwner isArchived"
            f' refs(refPrefix: "refs/tags/", first: {TAGS}{filtered},'
            " orderBy: {field: TAG_COMMIT_DATE, direction: DESC}) { nodes { name } }"
            " latestRelease { tagName } }"
        )
    return "query { rateLimit { remaining } " + " ".join(fields) + " }"


def read_repos(keys, token, today, before=None):
    """{key: {"tags", "release", "read", "archived"?, "name"?} or {"gone":
    True, "read"}} for those of keys GitHub answered, BATCH a query ("name":
    the repository's name now, when it isn't the key's); stops early when
    the token runs low or GitHub stops answering (the rest wait for the next
    run).

    A repository GitHub answers null for is gone only when it was the last
    read too (before: what the last runs had): a query GitHub fails can
    answer null for repositories that are there (25 on 2026-10-27, "Something
    went wrong while executing your query"). The first time, it keeps what it
    had, marked "missing". One left out of the answer isn't read."""
    found = {}
    in_a_row = 0
    for start in range(0, len(keys), BATCH):
        part = keys[start : start + BATCH]
        try:
            data = github.graphql(token, query(part), {})
            in_a_row = 0
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"  GitHub: {e}", file=sys.stderr)
            in_a_row += 1
            if in_a_row >= MAX_FAILURES_IN_A_ROW:
                print(
                    "::warning::GitHub stopped answering: the rest wait",
                    file=sys.stderr,
                )
                break
            continue
        for i, k in enumerate(part):
            if f"r{i}" not in data:
                continue  # not answered: read again next run
            repo = data[f"r{i}"]
            if repo is None:
                was = (before or {}).get(k) or {}
                if was.get("gone") or was.get("missing"):
                    found[k] = {"gone": True, "read": today}
                else:
                    found[k] = {
                        **was,
                        "missing": True,
                        "read": was.get("read") or today,
                    }
                continue
            release = (repo.get("latestRelease") or {}).get("tagName")
            # GitHub answers for an old name with the repository it
            # redirects to; a name only cased differently is the same one.
            name = repo.get("nameWithOwner")
            moved = name and name.lower() != k.split("|", 1)[0].lower()
            found[k] = {
                "tags": [
                    n["name"] for n in (repo.get("refs") or {}).get("nodes") or []
                ],
                **({"release": release} if release else {}),
                **({"archived": True} if repo.get("isArchived") else {}),
                **({"name": name} if moved else {}),
                "read": today,
            }
        if (data.get("rateLimit") or {}).get("remaining", MIN_POINTS) < MIN_POINTS:
            print(
                "::warning::GitHub token low on points: the rest wait", file=sys.stderr
            )
            break
        time.sleep(PAUSE)
    return found


def due(keys, repos, outdated):
    """keys in the order to read them, all of them each run: those never
    read, then those of packages Repology calls outdated (outdated: their
    keys), then the rest, the longest unread first, so what a run cut short
    didn't reach goes first the next time."""
    ordered = sorted(keys)
    never = [k for k in ordered if k not in repos]
    urgent = [k for k in ordered if k in outdated and k in repos]
    rest = sorted(
        (k for k in ordered if k in repos), key=lambda k: repos[k].get("read") or ""
    )
    return list(dict.fromkeys(never + urgent + rest))


def newest(check, repo):
    """{"tag"?, "release"?}: the newest of repo's tags matching check's
    pattern, by Repology's version order, and its latest release's version
    when that matches."""
    rule = re.compile(check["tags"])
    found = {}
    versions = [m.group(1) for t in repo.get("tags") or [] if (m := rule.match(t))]
    if versions:
        found["tag"] = max(versions, key=version_key)
    if (m := rule.match(repo.get("release") or "")) is not None:
        found["release"] = m.group(1)
    return found


def read_state(directory):
    try:
        with gzip.open(os.path.join(directory, STATE), "rt") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def write_json(directory, name, body):
    os.makedirs(directory, exist_ok=True)
    data = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    with open(os.path.join(directory, name), "wb") as f:
        f.write(gzip.compress(data, compresslevel=9, mtime=0))


def read(directory):
    """releases.json.gz in directory, or None if there's none."""
    try:
        with gzip.open(os.path.join(directory, FILE), "rt") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def outdated_keys(state, outdated_attrs):
    """The keys of the packages among outdated_attrs (Repology's outdated)."""
    checks = state.get("checks") or {}
    return {key(checks[a]) for a in outdated_attrs if a in checks}


def update(directory, now, outdated_attrs=(), on_evaluated=None):
    """Bring releases.json.gz up to date: {"at", "packages", "read", ...}
    for meta.json, or None when nothing could be done (no evaluation yet
    and none possible, or no token): the last files stay. When it evaluates
    where packages come from (weekly), on_evaluated(revision, {attr: src})
    gets every package's src (packages.update)."""
    state = read_state(directory)
    evaluated = state.get("evaluatedAt")
    if not evaluated or now - datetime.fromisoformat(evaluated) >= EVALUATE_EVERY:
        print("Evaluating where packages come from (GitHub tags)...", file=sys.stderr)
        try:
            revision = nixpkgs.channel_revision()
            attrs = [a for a in nixpkgs.load_index() if not in_bulk_set(a)]
            sources = {}
            checks = evaluate(revision, attrs, sources)
            if len(checks) < MIN_CHECKS:
                raise ValueError(
                    f"only {len(checks):,} packages with a GitHub tag check "
                    f"(expected over {MIN_CHECKS:,})"
                )
            state.update(
                checks=checks,
                revision=revision,
                evaluatedAt=now.isoformat(timespec="seconds"),
            )
            print(
                f"  {len(checks):,} packages with a GitHub tag check", file=sys.stderr
            )
            if on_evaluated:
                on_evaluated(revision, sources)
        except (nixpkgs.EvalError, urllib.error.URLError, OSError, ValueError) as e:
            print(f"::warning::GitHub releases: no evaluation ({e})", file=sys.stderr)
            if not state.get("checks"):
                return None
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GitHub releases: no GITHUB_TOKEN, nothing read", file=sys.stderr)
        return None
    checks = state["checks"]
    repos = state.setdefault("repos", {})
    keys = {key(c) for c in checks.values()}
    for gone in set(repos) - keys:  # no package reads it any more
        del repos[gone]
    state.pop("rotation", None)  # the weekly rotation's place, before
    chosen = due(keys, repos, outdated_keys(state, outdated_attrs))
    print(f"Reading {len(chosen):,} repositories on GitHub...", file=sys.stderr)
    read_now = read_repos(chosen, token, now.date().isoformat(), repos)
    repos.update(read_now)
    write_json(directory, STATE, {"format": FORMAT, **state})
    packages = {}
    for attr, check in sorted(checks.items()):
        repo = repos.get(key(check))
        if not repo:
            continue
        if repo.get("gone"):
            packages[attr] = {
                "repo": check["repo"],
                "version": check["version"],
                "gone": True,
                "read": repo["read"],
            }
            continue
        packages[attr] = {
            "repo": check["repo"],
            "version": check["version"],
            **newest(check, repo),
            **({"archived": True} if repo.get("archived") else {}),
            **({"movedTo": repo["name"]} if repo.get("name") else {}),
            "read": repo["read"],
        }
    gone = sorted({k.split("|", 1)[0] for k, r in repos.items() if r.get("gone")})
    write_json(
        directory,
        FILE,
        {
            "format": FORMAT,
            "fetchedAt": now.isoformat(timespec="seconds"),
            "evaluatedAt": state["evaluatedAt"],
            "revision": state.get("revision"),
            "packages": packages,
            "gone": gone,
        },
    )
    archived = sum(1 for p in packages.values() if p.get("archived"))
    moved = sum(1 for p in packages.values() if p.get("movedTo"))
    print(
        f"  read {len(read_now):,}; {len(packages):,} packages, {len(gone):,} "
        f"repositories gone; {archived:,} packages archived, {moved:,} moved",
        file=sys.stderr,
    )
    return {
        "at": now.isoformat(timespec="seconds"),
        "evaluatedAt": state["evaluatedAt"],
        "packages": len(packages),
        "read": len(read_now),
    }
