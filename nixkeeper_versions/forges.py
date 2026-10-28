"""Other forges than GitHub: GitLab instances and Forgejo (Codeberg).

For each repository: its newest tags, its latest release, whether it's
archived, and its name now; the same as releases.py reads from GitHub,
with no token. A GitLab instance answers a batch of projects in one
GraphQL query (archived, latest release, path), except those that refuse
it (NO_GRAPHQL); then each project's tags in one REST request. One the
batch leaves out (renamed: GraphQL doesn't follow an old path) or on an
instance without it is asked by REST too, which redirects an old path to
the project, so its path now says whether it was renamed. On Forgejo, one
REST request each for the repository (archived, its name now: an old name
redirects to the new one), its tags and its latest release. Each site is
asked at most once every SITE_PAUSE_SECONDS (pace), the sites side by side,
and every request names nixkeeper (its User-Agent). gitlab.com is asked
with a token when there's one (GITLAB_TOKEN).
"""

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from nixkeeper import config as nixkeeper_config
from nixkeeper.sources import http

# The tags read per repository: the newest, by version on GitLab, by
# creation on Forgejo.
TAGS = 100
# Projects per GitLab GraphQL query.
GRAPHQL_BATCH = 25
# GitLab instances whose GraphQL refuses requests that aren't a browser's
# (gitlab.gnome.org, 406, 2026-10-28): REST only, without "archived" (REST
# leaves it out without a token).
NO_GRAPHQL = frozenset({"gitlab.gnome.org"})
# Seconds a GraphQL query may take: one that doesn't answer in time leaves
# its projects to REST (gitlab.com timed out at 60 s once, 2026-10-28).
GRAPHQL_TIMEOUT = 20
# The sites asked with GITLAB_TOKEN when it's set: a token's limits are per
# account, not per address, which GitHub's runners share with everyone
# else's requests (gitlab.com answered 429 after 43 projects, asking to wait
# 25 minutes, 2026-10-28). The token is read_api on an account in no group
# or project: it reads only what anyone can.
TOKEN_HOSTS = frozenset({"gitlab.com"})
# The path GitLab gives a project waiting to be deleted (it redirects the
# old path there): as good as gone (gitlab.com's pizarra-gtk, 2026-10-28).
DELETING = re.compile(r"-deletion_scheduled-\d+$")
# Stop asking a site after so many failures in a row: the rest wait for the
# next run.
MAX_FAILURES_IN_A_ROW = 3
# The errors a request can end with.
FAILED = (urllib.error.URLError, OSError, ValueError, KeyError, TypeError)


# When each site was last asked, and a lock per site. Process-wide on
# purpose: the pause is between any two requests to a site. One lock per
# site, so a site's wait doesn't hold up another's (nixkeeper's http.pace
# waits holding one lock for every site).
_last_asked = {}
_site_locks = {}
_locks_lock = threading.Lock()


def pace(url):
    """Wait so url's site is asked at most once every SITE_PAUSE_SECONDS."""
    site = urllib.parse.urlsplit(url).netloc.lower()
    with _locks_lock:
        lock = _site_locks.setdefault(site, threading.Lock())
    with lock:
        pause = nixkeeper_config.SITE_PAUSE_SECONDS
        wait = _last_asked.get(site, float("-inf")) + pause - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_asked[site] = time.monotonic()


def split(key):
    """(forge, host, path, prefix) of a key: "gitlab:host/path|prefix"."""
    where, prefix = key.split("|", 1)
    forge, repo = where.split(":", 1)
    host, path = repo.split("/", 1)
    return forge, host, path, prefix


def token_header(host):
    """{"Authorization"} with GITLAB_TOKEN for host, or {} without one."""
    token = os.environ.get("GITLAB_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token and host in TOKEN_HOSTS else {}


def get_json(url):
    """url's JSON, paced per site; None on a 404.

    A request the token is refused for (403) is asked again without it, at
    once rather than after http's retries: a token can be missing a
    permission a project asks for (a fine-grained one, on gitlab.com's gajim
    projects, 2026-10-28) where anyone may read.
    """
    pace(url)
    host = urllib.parse.urlsplit(url).netloc.lower()
    token = token_header(host)
    try:
        body = http.get(
            url,
            accept="application/json",
            headers=token,
            fail_on=(403,) if token else (),
        )
    except urllib.error.HTTPError as e:
        if e.code != 403 or not token:
            raise
        print(
            f"  {host}: the token was refused ({refusal(e)}): asked without it",
            file=sys.stderr,
        )
        pace(url)
        body = http.get(url, accept="application/json")
    return None if body is None else json.loads(body)


def refusal(err):
    """What a refused request's answer says, shortened; "" when unreadable."""
    try:
        answer = json.loads(err.read())
    except (OSError, ValueError, AttributeError):
        return ""
    finally:
        err.close()
    if not isinstance(answer, dict):
        return ""
    return str(answer.get("error_description") or answer.get("message") or "")[:200]


def graphql(host, query):
    """A GitLab instance's answer to a GraphQL query, paced; its data."""
    url = f"https://{host}/api/graphql"
    pace(url)
    request = urllib.request.Request(
        url,
        data=json.dumps({"query": query}).encode(),
        headers={
            "User-Agent": nixkeeper_config.user_agent(),
            "Content-Type": "application/json",
            "Accept": "application/json",
            **token_header(host),
        },
    )
    with urllib.request.urlopen(request, timeout=GRAPHQL_TIMEOUT) as resp:
        return json.loads(resp.read()).get("data") or {}


def gitlab_batch(host, paths):
    """{path in lowercase: {"path", "archived", "release"?}} of the paths
    GraphQL answers for (a renamed project's old path it leaves out)."""
    found = {}
    for start in range(0, len(paths), GRAPHQL_BATCH):
        part = paths[start : start + GRAPHQL_BATCH]
        query = (
            f"{{ projects(fullPaths: {json.dumps(part)}) {{ nodes {{ fullPath "
            "archived releases(first: 1, sort: RELEASED_AT_DESC) { nodes { "
            "tagName upcomingRelease } } } } }"
        )
        try:
            data = graphql(host, query)
        except FAILED as e:
            print(f"  {host} GraphQL: {e}", file=sys.stderr)
            continue
        for node in ((data.get("projects") or {}).get("nodes")) or []:
            releases = (node.get("releases") or {}).get("nodes") or []
            release = next(
                (r["tagName"] for r in releases if not r.get("upcomingRelease")), None
            )
            found[node["fullPath"].lower()] = {
                "path": node["fullPath"],
                "archived": bool(node.get("archived")),
                **({"release": release} if release else {}),
            }
    return found


def gitlab(host, keys, today):
    """{key: repo, or None when the instance doesn't know it} for keys on a
    GitLab instance; those not read are left out."""
    batch = {}
    if host not in NO_GRAPHQL:
        batch = gitlab_batch(host, sorted({split(k)[2] for k in keys}))
    found, in_a_row = {}, 0
    for k in keys:
        _, _, path, _ = split(k)
        project = urllib.parse.quote(path, safe="")
        api = f"https://{host}/api/v4/projects/{project}"
        try:
            known = batch.get(path.lower())
            if known is None:
                # Left out of the batch, or no batch: REST says where it is.
                answer = get_json(api)
                if answer is None:
                    found[k] = None
                    in_a_row = 0
                    continue
                if DELETING.search(answer["path_with_namespace"]):
                    found[k] = None
                    in_a_row = 0
                    continue
                known = {"path": answer["path_with_namespace"]}
            tags = get_json(
                f"{api}/repository/tags?order_by=version&sort=desc&per_page={TAGS}"
            )
            in_a_row = 0
        except FAILED as e:
            print(f"  {host}/{path}: {e}", file=sys.stderr)
            in_a_row += 1
            if in_a_row >= MAX_FAILURES_IN_A_ROW:
                print(
                    f"::warning::{host} stopped answering: the rest wait",
                    file=sys.stderr,
                )
                break
            continue
        found[k] = repo(
            host,
            path,
            [t["name"] for t in tags or []],
            known.get("release"),
            known.get("archived"),
            known["path"],
            today,
        )
    return found


def forgejo(host, keys, today):
    """{key: repo, or None when the instance doesn't know it} for keys on a
    Forgejo instance; those not read are left out."""
    found, in_a_row = {}, 0
    for k in keys:
        _, _, path, _ = split(k)
        api = f"https://{host}/api/v1/repos/{path}"
        try:
            answer = get_json(api)
            if answer is None:
                found[k] = None
                in_a_row = 0
                continue
            tags = get_json(f"{api}/tags?limit={TAGS}")
            latest = get_json(f"{api}/releases/latest")
            in_a_row = 0
        except FAILED as e:
            print(f"  {host}/{path}: {e}", file=sys.stderr)
            in_a_row += 1
            if in_a_row >= MAX_FAILURES_IN_A_ROW:
                print(
                    f"::warning::{host} stopped answering: the rest wait",
                    file=sys.stderr,
                )
                break
            continue
        found[k] = repo(
            host,
            path,
            [t["name"] for t in tags or []],
            (latest or {}).get("tag_name"),
            answer.get("archived"),
            answer["full_name"],
            today,
        )
    return found


def repo(host, path, tags, release, archived, now_path, today):
    """A repository's state, as releases.py keeps GitHub's: {"tags",
    "release"?, "archived"?, "name"? (host/path now, when it isn't path,
    case aside), "read"}."""
    moved = now_path.lower() != path.lower()
    return {
        "tags": tags,
        **({"release": release} if release else {}),
        **({"archived": True} if archived else {}),
        **({"name": f"{host}/{now_path}"} if moved else {}),
        "read": today,
    }


def read(keys, today):
    """{key: repo, or None when its forge doesn't know it} for keys on
    other forges ("gitlab:..." and "forgejo:..."), the sites side by side
    (each still paced on its own); those not read this run are left out."""
    by_site = {}
    for k in keys:
        forge, host, _, _ = split(k)
        by_site.setdefault((forge, host), []).append(k)

    def site(item):
        (forge, host), site_keys = item
        print(f"  {host}: {len(site_keys):,} repositories...", file=sys.stderr)
        reader = gitlab if forge == "gitlab" else forgejo
        return reader(host, site_keys, today)

    found = {}
    if by_site:
        with ThreadPoolExecutor(max_workers=len(by_site)) as pool:
            for answers in pool.map(site, sorted(by_site.items())):
                found.update(answers)
    return found
