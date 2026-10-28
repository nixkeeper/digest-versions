# nixkeeper-versions

A digest of [Repology](https://repology.org)'s data for every nixpkgs
project, for [nixkeeper](https://github.com/nixkeeper/nixkeeper): each
project's versions in every repository Repology knows, kept up to date every
12 hours by a workflow, so nixkeeper reads one file instead of asking Repology about
each of its packages.

It reads Repology the way nixpkgs-update does, from its lists of nixpkgs'
projects, 200 to a request, and less often for what changes less often. All
of nixpkgs costs about 650 requests a day in two runs (at most 930),
whatever nixkeeper tracks, within Repology's [API rules](https://repology.org/api): at most one request
a second, under 1,000 a day, with a User-Agent linking here.

## The digest

On the `data` branch:

- [`data/projects.jsonl.gz`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/projects.jsonl.gz)
  (about 12 MB): every Repology project with a nixpkgs package
  (`nix_unstable`), one per line, sorted by name:

  ```json
  {"project": "tracy", "checked": "2026-10-04",
   "entries": [{"repo": "nix_unstable", "srcname": "tracy_0_11",
                "version": "0.11.1", "status": "legacy"}, ...]}
  ```

  with each package of it in every repository, as Repology's API gives
  them, trimmed to `repo`, `version`, `status`, `srcname` (for nixpkgs, the
  package's attribute) and `vulnerable` (when `true`), each entry once; and
  the day it was last read (`checked`). One project per line, so a reader
  can keep only those it wants while reading: about 25 MB of memory instead
  of 1.4 GB for all of them at once.

- [`data/typst.json.gz`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/typst.json.gz)
  (about 17 KB): the newest version of each package on
  [Typst Universe](https://typst.app/universe/), the source of nixpkgs'
  `typstPackages`, whose versions Repology mostly can't compare, and the
  day it was published:

  ```json
  {"format": 1, "fetchedAt": "2026-10-07T04:10:00+00:00",
   "packages": {"cetz": {"version": "0.5.2", "released": "2026-05-07"}, ...}}
  ```

  From Universe's index, one request a run (2.3 MB, every version of every
  package). A run that can't read it keeps the last (`fetchedAt` says how
  old).

- [`data/emacs.json.gz`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/emacs.json.gz)
  (about 95 KB): the newest version of each package in each Emacs package
  archive nixpkgs' `emacsPackages` are made from (MELPA, MELPA Stable,
  NonGNU ELPA, GNU ELPA), whose versions Repology mostly can't compare
  (MELPA's are dates):

  ```json
  {"format": 1, "fetchedAt": "2026-10-08T04:10:00+00:00",
   "archives": {"melpa": {"magit": "20251005.508", ...},
                "melpaStable": {"rtags": "3.23", ...},
                "nongnu": {"jabber": "0.15.0", ...}, "gnu": {"auctex": "14.2.0", ...}}}
  ```

  MELPA's versions are the date and time of the commit it built. From the
  archives' indexes, the ones nixpkgs' update scripts read: four requests a
  run (MELPA's two as JSON, the ELPAs' as Emacs Lisp data, of which only
  each entry's name and version are read). A run that can't read all four
  keeps the last (`fetchedAt` says how old).

- [`data/stackage.json.gz`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/stackage.json.gz)
  (about 25 KB): the Stackage LTS series nixpkgs pins about 3,400 of its
  `haskellPackages` to, and that series' newest version of each: for
  those, the version to be at, not Hackage's newest (Stackage holds newer
  ones back until its next series):

  ```json
  {"format": 1, "fetchedAt": "2026-10-08T04:10:00+00:00",
   "series": "lts-24", "nixpkgs": "lts-24.38", "snapshot": "lts-24.62",
   "versions": {"aeson": "2.2.5.1", ...}}
  ```

  Which series, and which packages, from `stackage.yaml` on nixpkgs master
  (its first line: "# Stackage LTS 24.38"); the versions from that
  series' newest snapshot on stackage.org (its `cabal.config`, as nixpkgs'
  own update script reads it). Two requests a run; a run that can't read
  either keeps the last.

- [`data/cran.json.gz`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/cran.json.gz):
  CRAN's and Bioconductor's packages and versions, which nixpkgs'
  `rPackages` are generated from: CRAN as it is that day, and
  Bioconductor's release that nixpkgs pins (read from the top of
  `bioc-packages.json` on nixpkgs master), its software, annotation and
  experiment packages:

  ```json
  {"format": 1, "fetchedAt": "2026-10-08T04:10:00+00:00",
   "biocVersion": "3.23",
   "cran": {"ggplot2": "4.0.1", ...}, "bioc": {"AnVIL": "1.24.1", ...},
   "annotation": {...}, "experiment": {...}}
  ```

  From their `PACKAGES.gz` index files, as R and nixpkgs' own update script
  read them: five requests a run. A package nixpkgs has in none of them is
  no longer on CRAN (archived) or in that Bioconductor release. A run that
  can't read all of them keeps the last.

- [`data/releases.json.gz`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/releases.json.gz):
  for each nixpkgs package fetched from a GitHub tag (about 34,000
  attributes outside the sets above, 21,000 repositories), its newest
  version on GitHub, so nixkeeper doesn't depend on Repology for them:

  ```json
  {"format": 1, "fetchedAt": "...", "evaluatedAt": "...", "revision": "...",
   "packages": {"whisky": {"repo": "Whisky-App/Whisky", "version": "2.3.5",
                           "tag": "2.4.0", "release": "2.4.0", "read": "2026-10-08"}, ...},
   "elsewhere": {"tellico": {"forge": "gitlab", "repo": "invent.kde.org/office/tellico",
                             "version": "4.2.2", "tag": "4.3", "read": "2026-10-28"}, ...},
   "gone": ["owner/repo", ...]}
  ```

  `version` is nixpkgs'; `tag` the newest of the repository's tags that
  match the package's tag scheme (as nixkeeper's update checks work it out
  from its source: `v2.4.0` for `v2.3.5`), by Repology's version order;
  `release` its latest release's, when that matches (drafts and
  pre-releases left out); `read` when the repository was read. What became
  of the repository, when something did: `archived` (true: read-only, its
  owner's way of saying it's no longer worked on), `movedTo` (its name now,
  when GitHub answers for nixpkgs' name under another: renamed, or moved to
  another owner), or `gone` (true, with no `tag` or `release`: GitHub didn't
  know it two runs in a row, as one query GitHub fails can answer nothing
  for repositories that are there; also listed in the top-level `gone`).
  `elsewhere` has the same for packages fetched from a tag on a GitLab
  instance (gitlab.com, freedesktop, GNOME, KDE, Debian's salsa) or on
  Codeberg, about 1,350, with their `forge` (`gitlab`, `forgejo`) and
  `repo` as `host/path` (`movedTo` too; a GitLab project waiting to be
  deleted, which GitLab renames `...-deletion_scheduled-<id>`, counts as
  gone); apart from `packages` because
  nixkeeper read those as GitHub's before it knew of others. They're read
  without a token (gitlab.com with `GITLAB_TOKEN` when set: `read_api` on
  an account in no group or project, so its limits are that account's, not
  the runner's shared address's; a request the token is refused for is
  asked again without it), each site at most once a second, the
  sites side by side: on GitLab a GraphQL query per 25 projects (archived, latest release, path; not on GNOME,
  which refuses GraphQL that isn't a browser's, so it has no `archived`),
  then each project's tags; on Codeberg each repository, its tags and its
  latest release. A release older than nixpkgs' version is left out there
  (one nobody updated: KDE's tellico), so the tags decide. Where each
  package comes from is evaluated from nixpkgs weekly (with Nix and
  nixkeeper's own code, this flake's input: about a minute); each run reads
  every repository (about 21,400 on 2026-10-11, an hour), so a release
  shows within 12 hours: those never read first, then those of packages
  Repology has outdated, then the longest unread, 25 a GraphQL query (about
  860 points) with `NIXKEEPER_VERSIONS_TOKEN`, a personal token that only
  needs to read public repositories (5,000 points an hour), else the
  workflow's own (1,000: a run that gets low stops, and reads what it
  missed first the next day). `releases-state.json.gz` keeps what that needs
  between runs. A run that can't evaluate or reach GitHub keeps the last.

- [`data/nixpkgs.json.gz`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/nixpkgs.json.gz):
  nixpkgs' own facts about its packages, for readers without Nix (such as
  [nixkeeper-vulnerabilities](https://github.com/nixkeeper/digest-vulnerabilities),
  which matches packages to advisories by registry name and version, and
  by source repository and tag or commit):

  ```json
  {"format": 1, "indexedAt": "...", "revision": "...",
   "evaluatedAt": "...", "evaluatedRevision": "...",
   "packages": {"python313Packages.requests": {"pname": "requests", "version": "2.32.5"},
                "ripgrep": {"pname": "ripgrep", "version": "15.1.0",
                            "src": {"gitRepoUrl": "https://github.com/BurntSushi/ripgrep.git",
                                    "tag": "15.1.0", "url": "..."}}, ...}}
  ```

  Every attribute's `pname` and `version`, from the channel's package
  index (read daily, `revision` its commit); and `src`, where its source
  comes from (the fields it has of `gitRepoUrl`, `tag`, `rev`, `url`), for
  the attributes the weekly evaluation above covers (outside the sets
  nixkeeper compares with their own sources). A run that can't read the
  index keeps the last.

- [`data/meta.json`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/meta.json):
  when the outdated projects were last read (`outdatedAt`), how many there
  are, where the weekly rotation is (`rotation.next`, and `rotation.lapAt`,
  when it last went through all of them), the dropped projects' attributes
  looked up again and found in another project (`moved`: `asked`,
  `found`), how many projects there are, and
  how many requests the run made, and when Typst Universe's index, the
  Emacs archives, Stackage, CRAN and Bioconductor and GitHub were last read
  and how many packages they had (`typst`, `emacs`, `stackage`, `cran`,
  `releases`), and when nixpkgs' index was read and its sources evaluated,
  with how many attributes and sources (`nixpkgs`).

The `data` branch is `main` plus one commit with the digest: each run replaces
it, so no history piles up.

## How it's kept up to date

The "Digest" workflow runs every 12 hours, started from outside GitHub at
04:15 and 16:15 UTC (nixkeeper's `start-runs`), each run done (in about 75
minutes) before one of nixkeeper's syncs; its own schedule (05:37 and
17:37, often hours late) is the fallback, and stops when the digest is
under 10 hours old:

1. **Outdated projects, every run**: Repology's list of nixpkgs' outdated
   projects, all of it (about 70 requests), as nixpkgs-update reads it. Every
   outdated package's versions are at most 12 hours old.
2. **No longer outdated, every run**: the projects that were outdated and
   aren't on the list now (updated in nixpkgs, mostly), read one by one (up
   to 250 a run).
3. **All projects, every week**: a fourteenth of all nixpkgs' projects (45
   pages), going on where the last run stopped: up-to-date projects' other
   repositories, legacy versions (not on the outdated list), and
   vulnerabilities on versions that aren't outdated (Repology has no list
   of those) are at most a week old. A project that's no longer there is
   dropped.
4. **Renamed projects, the same day**: for each nixpkgs attribute of a
   project dropped in steps 2 and 3 that no project has any more, Repology
   is asked which project it's in now, one request each (its `project-by`
   tool, up to 100 a run). Repology renames or splits projects now and then
   (2026-10-08: `urlencode` became `urlencode-dead10ck`, the old name kept
   by another program): without this, the attribute would be missing until
   the rotation reached the new name, up to a week, and nixkeeper would keep
   the old project's versions meanwhile. An attribute removed from nixpkgs
   costs one request that finds nothing.

The first run, with no digest yet, reads all projects at once (about 600
requests, an hour); so does starting the workflow by hand with "full". A
run that fails, or gets far fewer projects than nixpkgs has, publishes
nothing: the last digest stays, and nixkeeper asks Repology itself for what
it lacks.

## Running it

```bash
nix run . -- data
```

brings the digest in `data/` up to date (`-- data --full` reads all
projects; `--if-older 10` only when the last run is over 10 hours old, so a
run started twice sweeps once; `--sources-only` the other sources only,
Typst, Emacs, Stackage, CRAN and GitHub, with no request to Repology, to
try them without spending its daily allowance: "sources_only" when
starting the workflow by hand). `nix flake check` runs the tests and lint, `nix fmt` formats.

## AI & automation disclosure

This digest's code and documentation are written with the assistance of an
AI (Claude, by Anthropic), with a person in the loop: nixkeeper's maintainer
decides what to build, and reviews and tests every change before it's
merged. The digest itself is automation: a scheduled workflow that reads
public sources and publishes what it finds. It never comments on, closes or
merges anything in nixpkgs.

## License

MIT
