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
files.

Packages pinned to a GitHub commit with an unstable version
(0-unstable-2024-05-01; nixkeeper.inferred.pin_check) have no version to
compare: their commit is compared with the repository's default branch
instead, and their release tags read as the others', in a section of their
own ("pins"):

    "pins": {"_2048-in-terminal": {"repo": "alewmoose/2048-in-terminal",
                                   "version": "0-unstable-2022-06-13",
                                   "commit": "bf22f86...",
                                   "branch": {"status": "identical", "ahead": 0,
                                              "behind": 0,
                                              "lastCommit": "2022-06-13"},
                                   "read": "2026-10-30"}, ...}

"status" is GitHub's for the commit against the default branch: "identical"
(the branch's newest), "behind" (by "behind" commits), "ahead" or
"diverged" (on another branch: its commits aren't all on the default
one), or "unknown" when GitHub couldn't compare (the commit isn't in the
repository any more); "lastCommit" is the default branch's newest commit's
date. "tag" and "release" as the others', and "releasedAt", the release's
date: a release older than the pin isn't one since."""

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

from . import forges

FILE = "releases.json.gz"
STATE = "releases-state.json.gz"
FORMAT = 1
# The evaluation of where packages come from: weekly, a chunk of attributes
# per nix eval (memory stays small), and so many checks at the least (about
# 23,000 on 2026-10-07): fewer, something went wrong.
EVALUATE_EVERY = timedelta(days=7)
# The checks' version: 2 since packages from GitLab instances and Codeberg
# have one too (inferred.forge_check, 2026-10-28), 3 since those fetched
# from a commit with a release's version do (2026-10-29), 4 since those
# pinned to a commit with an unstable version do (inferred.pin_check,
# 2026-10-30). A state from before is evaluated again at once, not a week
# later.
CHECKS_VERSION = 4
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
    """{attr: {"repo", "tags", "version", "forge"?, "commit"?, "date"?}} for
    the attrs fetched from a tag on GitHub, a GitLab instance or Codeberg
    ("forge": "gitlab" or "forgejo", "repo" then "host/path"), with the
    pattern their tags must match (inferred.forge_check), and those pinned
    to a GitHub commit with an unstable version ("commit" and its "date";
    inferred.pin_check): src
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
            check, _ = inferred.forge_check(src, attr)
            if not check:
                check, _ = inferred.pin_check(src, attr)
            if not check:
                continue
            forge = next(f for f in ("github", "gitlab", "forgejo") if f in check)
            found[attr] = {
                "repo": check[forge],
                "tags": check["tags"],
                "version": src.get("version") or "",
                **({"forge": forge} if forge != "github" else {}),
                **{k: check[k] for k in ("commit", "date") if k in check},
            }
    return found


def prefix(pattern):
    """The literal start of a tag pattern ("^v(...)$": "v"; monorepos' are
    longer: "azure-search-documents_"), or ""."""
    head = pattern.removeprefix("^").split("(", 1)[0]
    return re.sub(r"\\(.)", r"\1", head)


def key(check):
    """The repository and tag prefix a check reads: "owner/repo|prefix" on
    GitHub, "gitlab:host/path|prefix" or "forgejo:host/owner/repo|prefix"
    elsewhere; a pin's commit after them ("owner/repo|prefix|commit")."""
    where = f"{check['forge']}:{check['repo']}" if check.get("forge") else check["repo"]
    pinned = f"|{check['commit']}" if check.get("commit") else ""
    return f"{where}|{prefix(check['tags'])}{pinned}"


def on_github(k):
    """Whether key k is a GitHub repository's (no "forge:" before it)."""
    return ":" not in k.split("|", 1)[0]


def is_pin(k):
    """Whether key k is a pin's (a commit after its prefix)."""
    return k.count("|") == 2


def not_found(was, today):
    """A repository's state once its forge doesn't know it: gone only when
    it wasn't found the last read either (was: the state before), as a
    query the forge fails can answer null for repositories that are there
    (25 on GitHub, 2026-10-27, "Something went wrong while executing your
    query"). The first time, what it had, marked "missing"."""
    was = was or {}
    if was.get("gone") or was.get("missing"):
        return {"gone": True, "read": today}
    return {**was, "missing": True, "read": was.get("read") or today}


def query(keys, compare=True):
    """One GraphQL query for keys' repositories: each one's newest TAGS tags
    by commit date (only those containing its prefix, for monorepos), and
    its latest release; for a pin, the release's date and the default
    branch's newest commit's, and the pin's commit compared with that
    branch (unless compare is False)."""
    fields = []
    for i, k in enumerate(keys):
        repo, start, *commit = k.split("|")
        owner, name = repo.split("/", 1)
        filtered = f", query: {json.dumps(start)}" if len(start) > 1 else ""
        pin = ""
        if commit:
            compared = (
                f" compare(headRef: {json.dumps(commit[0])})"
                " { status aheadBy behindBy }"
                if compare
                else ""
            )
            pin = (
                " defaultBranchRef { target { ... on Commit { committedDate } }"
                f"{compared} }}"
            )
        fields.append(
            f"r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{"
            " nameWithOwner isArchived"
            f' refs(refPrefix: "refs/tags/", first: {TAGS}{filtered},'
            " orderBy: {field: TAG_COMMIT_DATE, direction: DESC}) { nodes { name } }"
            f" latestRelease {{ tagName{' publishedAt' if commit else ''} }}{pin} }}"
        )
    return "query { rateLimit { remaining } " + " ".join(fields) + " }"


def ask(token, keys):
    """GitHub's answer to query(keys). GitHub fails the whole query when one
    pin's commit isn't in its repository ("Something went wrong while
    executing your query", 2026-10-30): an answer with none of the
    repositories, for a query with pins, is asked again a repository at a
    time, and a lone pin's without the comparison."""
    data = github.graphql(token, query(keys), {})
    if any(f"r{i}" in data for i in range(len(keys))) or not any(map(is_pin, keys)):
        return data
    if len(keys) == 1:
        return github.graphql(token, query(keys, compare=False), {})
    found = {}
    for i, k in enumerate(keys):
        one = ask(token, [k])
        if "r0" in one:
            found[f"r{i}"] = one["r0"]
        if "rateLimit" in one:
            found["rateLimit"] = one["rateLimit"]
    return found


def branch(repo):
    """A pin's commit against its repository's default branch, from its
    answer: {"status", "ahead", "behind", "lastCommit"}, "status" "unknown"
    when GitHub didn't compare them."""
    default = repo.get("defaultBranchRef") or {}
    compared = default.get("compare")
    last = ((default.get("target") or {}).get("committedDate") or "")[:10]
    return {
        **(
            {
                "status": compared["status"].lower(),
                "ahead": compared["aheadBy"],
                "behind": compared["behindBy"],
            }
            if compared
            else {"status": "unknown"}
        ),
        **({"lastCommit": last} if last else {}),
    }


def read_repos(keys, token, today, before=None):
    """{key: {"tags", "release", "read", "archived"?, "name"?, "branch"?,
    "releasedAt"?} or {"gone": True, "read"}} for those of keys GitHub
    answered, BATCH a query ("name": the repository's name now, when it
    isn't the key's; "branch" and "releasedAt" for a pin's); stops early when
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
            data = ask(token, part)
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
                found[k] = not_found((before or {}).get(k), today)
                continue
            latest = repo.get("latestRelease") or {}
            release = latest.get("tagName")
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
                **(
                    {
                        "branch": branch(repo),
                        **(
                            {"releasedAt": latest["publishedAt"][:10]}
                            if latest.get("publishedAt")
                            else {}
                        ),
                    }
                    if is_pin(k)
                    else {}
                ),
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


def is_older(version, than):
    """Whether version is older than than, by Repology's order (no version:
    no)."""
    return bool(version) and version_key(version) < version_key(than)


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
    if (
        not evaluated
        or now - datetime.fromisoformat(evaluated) >= EVALUATE_EVERY
        or state.get("checksVersion") != CHECKS_VERSION
    ):
        print("Evaluating where packages come from (forge tags)...", file=sys.stderr)
        try:
            revision = nixpkgs.channel_revision()
            attrs = [a for a in nixpkgs.load_index() if not in_bulk_set(a)]
            sources = {}
            checks = evaluate(revision, attrs, sources)
            if len(checks) < MIN_CHECKS:
                raise ValueError(
                    f"only {len(checks):,} packages with a tag check "
                    f"(expected over {MIN_CHECKS:,})"
                )
            state.update(
                checks=checks,
                revision=revision,
                evaluatedAt=now.isoformat(timespec="seconds"),
                checksVersion=CHECKS_VERSION,
            )
            elsewhere = sum(1 for c in checks.values() if c.get("forge"))
            pinned = sum(1 for c in checks.values() if c.get("commit"))
            print(
                f"  {len(checks) - pinned:,} packages with a tag check, "
                f"{elsewhere:,} of them on GitLab or Codeberg, and {pinned:,} "
                "pinned to a GitHub commit",
                file=sys.stderr,
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
    today = now.date().isoformat()
    on_hub = [k for k in chosen if on_github(k)]
    print(f"Reading {len(on_hub):,} repositories on GitHub...", file=sys.stderr)
    read_now = read_repos(on_hub, token, today, repos)
    elsewhere_keys = [k for k in chosen if not on_github(k)]
    print(
        f"Reading {len(elsewhere_keys):,} repositories on GitLab and Codeberg...",
        file=sys.stderr,
    )
    for k, found in forges.read(elsewhere_keys, today).items():
        read_now[k] = found if found is not None else not_found(repos.get(k), today)
    repos.update(read_now)
    write_json(directory, STATE, {"format": FORMAT, **state})
    packages, elsewhere, pins = {}, {}, {}
    for attr, check in sorted(checks.items()):
        repo = repos.get(key(check))
        if not repo:
            continue
        # Another forge's, and pins, in sections of their own: nixkeeper
        # read "packages" as GitHub's tags before it knew of others.
        if check.get("commit"):
            into, where = pins, {"commit": check["commit"]}
        elif check.get("forge"):
            into, where = elsewhere, {"forge": check["forge"]}
        else:
            into, where = packages, {}
        if repo.get("gone"):
            into[attr] = {
                **where,
                "repo": check["repo"],
                "version": check["version"],
                "gone": True,
                "read": repo["read"],
            }
            continue
        found = newest(check, repo)
        # On another forge a "latest release" can be one nobody updated
        # since (KDE's tellico: v3.5.5, its tags at v4.3): older than
        # nixpkgs' version, it's left out and the tags decide.
        if check.get("forge") and is_older(found.get("release"), check["version"]):
            del found["release"]
        into[attr] = {
            **where,
            "repo": check["repo"],
            "version": check["version"],
            **found,
            **{k: repo[k] for k in ("branch", "releasedAt") if k in repo},
            **({"archived": True} if repo.get("archived") else {}),
            **({"movedTo": repo["name"]} if repo.get("name") else {}),
            "read": repo["read"],
        }
    gone = sorted(
        {k.split("|", 1)[0] for k, r in repos.items() if r.get("gone") and on_github(k)}
    )
    write_json(
        directory,
        FILE,
        {
            "format": FORMAT,
            "fetchedAt": now.isoformat(timespec="seconds"),
            "evaluatedAt": state["evaluatedAt"],
            "revision": state.get("revision"),
            "packages": packages,
            "elsewhere": elsewhere,
            "pins": pins,
            "gone": gone,
        },
    )
    every = [*packages.values(), *elsewhere.values(), *pins.values()]
    archived = sum(1 for p in every if p.get("archived"))
    moved = sum(1 for p in every if p.get("movedTo"))
    print(
        f"  read {len(read_now):,}; {len(packages):,} packages on GitHub, "
        f"{len(elsewhere):,} elsewhere, {len(pins):,} pinned to a commit, "
        f"{len(gone):,} GitHub repositories gone; "
        f"{archived:,} packages archived, {moved:,} moved",
        file=sys.stderr,
    )
    return {
        "at": now.isoformat(timespec="seconds"),
        "evaluatedAt": state["evaluatedAt"],
        "packages": len(packages),
        "elsewhere": len(elsewhere),
        "pins": len(pins),
        "read": len(read_now),
    }
