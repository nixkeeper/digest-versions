# nixkeeper-versions

A digest of the upstream versions of nixpkgs' packages, for
[nixkeeper](https://github.com/nixkeeper/nixkeeper): for each package, the
newest version where it comes from, read from that source itself where
the digest can, kept up to date every 12 hours by a workflow, so nixkeeper
reads a few files instead of asking each source about each of its packages.

## Where the versions come from

Each package's own source first, and [Repology](https://repology.org) for
the rest:

| Source | For | File |
|---|---|---|
| GitHub's tags and releases | packages fetched from a tag on GitHub (about 34,000 attributes, 21,000 repositories) | `releases.json.gz` |
| GitLab instances' and Codeberg's tags and releases | packages fetched from a tag on gitlab.com, freedesktop, GNOME, KDE, Debian's salsa or Codeberg (about 1,400) | `releases.json.gz` (`elsewhere`) |
| [Typst Universe](https://typst.app/universe/) | `typstPackages` | `typst.json.gz` |
| The Emacs package archives (MELPA, MELPA Stable, NonGNU ELPA, GNU ELPA) | `emacsPackages` | `emacs.json.gz` |
| [Stackage](https://www.stackage.org) LTS | the 3,400 `haskellPackages` nixpkgs pins to it | `stackage.json.gz` |
| CRAN and Bioconductor | `rPackages` | `cran.json.gz` |
| CHICKEN's egg indexes, Akku's index, extensions.gnome.org, the Azure CLI's extension index | `chickenPackages_5`, `chickenPackages_6`, `akkuPackages`, `gnomeExtensions`, `azure-cli-extensions` | `sets.json.gz` |
| [RubyGems](https://rubygems.org) and [crates.io](https://crates.io) | the gems and crates nixpkgs fetches from them: `rubyPackages` (three sets, 428 gems) and about 250 applications | `registries.json.gz` |
| Repology | every nixpkgs project: the others' versions in every repository Repology knows, and the only source for the rest | `projects.jsonl.gz` |

Measured on 2026-10-28 (each run counts again: `coverage` in
[`meta.json`](#metajson)): of nixpkgs' 151,804 attributes, 77,700 are in sets
updated by their own import scripts (Emacs, Haskell, R, Typst, Common
Lisp, TeX Live; the indexes above cover Emacs, R, Typst and Stackage's
part of Haskell, and `sets.json.gz` five more sets, read by nixkeeper as
sets of their own once it knows them) or aren't versioned (darwin, freebsd); 35,500 have their
version read from their forge; about 34,000 (22,000 distinct sources: PyPI,
CPAN, GNOME Shell extensions, packages pinned to a GitHub commit rather
than a tag, and a long tail of other sites) have Repology alone, for now.
Every source is asked as its own rules ask: at most one request a second
per site, a User-Agent linking here, and its index files rather than one
request per package where it has them.

## The digest

On the `data` branch, in `data/`.

### Forges: `releases.json.gz`

[`data/releases.json.gz`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/releases.json.gz):
for each nixpkgs package fetched from a tag on a forge, its newest version
there, and for each one pinned to a GitHub commit with an unstable version,
how far its commit is behind:

```json
{"format": 1, "fetchedAt": "...", "evaluatedAt": "...", "revision": "...",
 "packages": {"whisky": {"repo": "Whisky-App/Whisky", "version": "2.3.5",
                         "tag": "2.4.0", "release": "2.4.0", "read": "2026-10-08"}, ...},
 "elsewhere": {"tellico": {"forge": "gitlab", "repo": "invent.kde.org/office/tellico",
                           "version": "4.2.2", "tag": "4.3", "read": "2026-10-28"}, ...},
 "pins": {"extrakto": {"repo": "laktak/extrakto", "version": "0-unstable-2025-07-27",
                       "commit": "...", "branch": {"status": "behind", "ahead": 0,
                       "behind": 8, "lastCommit": "2026-03-02"}, "read": "2026-10-30"}, ...},
 "gone": ["owner/repo", ...]}
```

- `packages` are GitHub's, `elsewhere` the other forges', with their
  `forge` (`gitlab`, `forgejo`) and `repo` as `host/path`: apart because
  nixkeeper read `packages` as GitHub's before it knew of others.
- `version` is nixpkgs'; `tag` the newest of the repository's tags that
  match the package's tag scheme (as nixkeeper's update checks work it out
  from its source: `v2.4.0` for `v2.3.5`), by Repology's version order;
  `release` its latest release's, when that matches (drafts and
  pre-releases left out; on the other forges, also one older than
  nixpkgs' version, which nobody updated: KDE's tellico); `read` when the
  repository was last read.
- What became of the repository, when something did: `archived` (true:
  read-only, its owner's way of saying it's no longer worked on),
  `movedTo` (its name now, when the forge answers for nixpkgs' name under
  another: renamed, or moved to another owner), or `gone` (true, with no
  `tag` or `release`: the forge didn't know it two runs in a row, as one
  failed query can answer nothing for repositories that are there; GitHub's
  are also listed in the top-level `gone`). A GitLab project waiting to be
  deleted, which GitLab renames `...-deletion_scheduled-<id>`, counts as
  gone.
- `pins` are the packages fetched from a GitHub commit with an unstable
  version (`0-unstable-2024-05-01`, `1.2.12-unstable-2026-07-05`): no
  version to compare, so their `commit` is compared with the default
  branch. `branch.status` is GitHub's: `identical` (the branch's newest
  commit), `behind` (by `behind` commits), `ahead` or `diverged` (the
  commit is on another branch, as SDL_image's on SDL 1.2), or `unknown`
  when GitHub couldn't compare (the commit isn't in the repository any
  more); `lastCommit` the default branch's newest commit's date. `tag` and
  `release` as above, with the tags of the release the version starts with
  (`1.2.12`), or any `v` + numbers for `0-unstable`; `releasedAt` the
  release's date, as one older than the pin isn't one since. The rest
  (`archived`, `movedTo`, `gone`, `read`) as above.

Where each package comes from is evaluated from nixpkgs weekly (with Nix
and nixkeeper's own code, this flake's input: about a minute). Each run
then reads every repository, so a release shows within 12 hours:

- **GitHub**: those never read first, then those of packages Repology has
  outdated, then the longest unread, 25 a GraphQL query (about 1,020
  points a run, 160 of them for the pins' 4,000 repositories and commits)
  with `NIXKEEPER_VERSIONS_TOKEN`, a personal token that only
  needs to read public repositories (5,000 points an hour), else the
  workflow's own (1,000: a run that gets low stops, and reads what it
  missed first the next day).
- **GitLab instances and Codeberg**: each site at most once a second, the
  sites side by side (about 15 minutes). On GitLab a GraphQL query per 25
  projects (archived, latest release, path; not on GNOME, which refuses
  GraphQL that isn't a browser's, so it has no `archived`), then each
  project's tags; on Codeberg each repository, its tags and its latest
  release. Without a token, except gitlab.com with `GITLAB_TOKEN` when set:
  `read_api` on an account in no group or project, so its limits are that
  account's, not the runner's shared address's; a request the token is
  refused for is asked again without it, at once.

`releases-state.json.gz` keeps what that needs between runs. A run that
can't evaluate or reach GitHub keeps the last.

### Package indexes

The indexes nixpkgs' generated sets are made from, whose versions Repology
mostly can't compare. A run that can't read one keeps the last
(`fetchedAt` says how old).

- [`data/typst.json.gz`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/typst.json.gz)
  (about 17 KB): the newest version of each package on Typst Universe, the
  source of `typstPackages`, and the day it was published:

  ```json
  {"format": 1, "fetchedAt": "2026-10-07T04:10:00+00:00",
   "packages": {"cetz": {"version": "0.5.2", "released": "2026-05-07"}, ...}}
  ```

  From Universe's index, one request a run (2.3 MB, every version of every
  package).

- [`data/emacs.json.gz`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/emacs.json.gz)
  (about 95 KB): the newest version of each package in each Emacs package
  archive `emacsPackages` are made from:

  ```json
  {"format": 1, "fetchedAt": "2026-10-08T04:10:00+00:00",
   "archives": {"melpa": {"magit": "20251005.508", ...},
                "melpaStable": {"rtags": "3.23", ...},
                "nongnu": {"jabber": "0.15.0", ...}, "gnu": {"auctex": "14.2.0", ...}}}
  ```

  MELPA's versions are the date and time of the commit it built. From the
  archives' indexes, the ones nixpkgs' update scripts read: four requests a
  run (MELPA's two as JSON, the ELPAs' as Emacs Lisp data, of which only
  each entry's name and version are read); a run keeps the last unless it
  reads all four.

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
  own update script reads it). Two requests a run.

- [`data/cran.json.gz`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/cran.json.gz):
  CRAN's and Bioconductor's packages and versions, which `rPackages` are
  generated from: CRAN as it is that day, and Bioconductor's release that
  nixpkgs pins (read from the top of `bioc-packages.json` on nixpkgs
  master), its software, annotation and experiment packages:

  ```json
  {"format": 1, "fetchedAt": "2026-10-08T04:10:00+00:00",
   "biocVersion": "3.23",
   "cran": {"ggplot2": "4.0.1", ...}, "bioc": {"AnVIL": "1.24.1", ...},
   "annotation": {...}, "experiment": {...}}
  ```

  From their `PACKAGES.gz` index files, as R and nixpkgs' own update script
  read them: five requests a run. A package nixpkgs has in none of them is
  no longer on CRAN (archived) or in that Bioconductor release; a run keeps
  the last unless it reads all of them.

- [`data/sets.json.gz`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/sets.json.gz):
  the indexes of five more generated sets, each read as nixpkgs' own update
  script reads it, each with its own `fetchedAt` (a set whose index can't
  be read keeps its last part):

  ```json
  {"format": 1, "fetchedAt": "2026-10-30T04:10:00+00:00",
   "sets": {"chickenPackages_5": {"fetchedAt": "...", "packages": {"7off": {"version": "1.33"}, ...}},
            "akkuPackages": {"fetchedAt": "...", "packages": {"chibi-crypto-md5": {"version": "0.10.0"}, ...}},
            "gnomeExtensions": {"fetchedAt": "...", "packages": {"color-picker@tuberry":
                                {"slug": "color-picker", "pk": 3396, "shells": {"49": "47", "50": "49"}}, ...}},
            "azure-cli-extensions": {"fetchedAt": "...", "packages": {"acat":
                                     {"versions": [["1.0.0b1", "2.61.0"], ...]}}}, ...}}
  ```

  - `chickenPackages_5`, `chickenPackages_6`: CHICKEN's egg index for that
    major version (`egg-tarballs/5/index.gz`, every version of every egg;
    nixpkgs' `update.sh` reads the same eggs from git), each egg's newest
    by Repology's version order. Two requests a run.
  - `akkuPackages`: Akku's `Akku-index.scm`, as nixpkgs' `update.sh` reads
    it, each package's newest, by its name in nixpkgs (a library name's
    parts joined with `-`). One request.
  - `gnomeExtensions`: extensions.gnome.org's extension query, 25 to a
    page, one page a second (135 pages, about 3 minutes, as nixpkgs'
    `update-extensions.py` walks it), by UUID: its slug and its newest
    version for each GNOME Shell from 40 on (`shells`); and `attrs`,
    which extension each nixpkgs attribute is (`{"gnomeExtensions.color-picker":
    "color-picker@tuberry"}`), from the sources in `nixpkgs.json.gz`,
    which name it by its UUID without `@`: slugs aren't unique (106 of
    nixpkgs' extensions share theirs with another).
  - `azure-cli-extensions`: the Azure CLI's `index.json`, as nixpkgs'
    `extensions-tool.py` reads it: each version, with the oldest Azure CLI
    it runs on (`azext.minCliCoreVersion`). One request.

- [`data/registries.json.gz`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/registries.json.gz):
  the newest versions of the gems and crates nixpkgs fetches from RubyGems
  and crates.io, each registry with its own `fetchedAt`, and `attrs`, which
  gem or crate each attribute is (from the sources in `nixpkgs.json.gz`: a
  crate's name is often not the attribute's):

  ```json
  {"format": 1, "fetchedAt": "2026-10-31T04:10:00+00:00",
   "registries": {"rubygems": {"fetchedAt": "...", "attrs": {"rubyPackages.rails": "rails", ...},
                               "packages": {"rails": {"version": "8.1.4"}, ...}},
                  "crates.io": {"fetchedAt": "...", "attrs": {"_0x": "ohx", ...},
                                "packages": {"pijul": {"version": "0.15.0",
                                                       "preRelease": "1.0.0-beta.24"}, ...}}}}
  ```

  - `rubygems`: each gem's newest release (`versions/<gem>/latest.json`),
    for the attributes whose source is a gem (`rubyPackages`,
    `rubyPackages_3_4`, `rubyPackages_4_0` and a few applications).
  - `crates.io`: each crate's newest version that isn't yanked or a
    pre-release, and its newest pre-release when that's newer
    (`preRelease`), from crates.io's sparse index.

  One request per gem or crate, a quarter of a second apart (about 650,
  11 minutes a run). A registry that can't be read, or whose answers fail
  for over a tenth of its names, keeps its last part.

### Repology: `projects.jsonl.gz`

[`data/projects.jsonl.gz`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/projects.jsonl.gz)
(about 12 MB): every Repology project with a nixpkgs package
(`nix_unstable`), one per line, sorted by name:

```json
{"project": "tracy", "checked": "2026-10-04",
 "entries": [{"repo": "nix_unstable", "srcname": "tracy_0_11",
              "version": "0.11.1", "status": "legacy"}, ...]}
```

with each package of it in every repository, as Repology's API gives them,
trimmed to `repo`, `version`, `status`, `srcname` (for nixpkgs, the
package's attribute) and `vulnerable` (when `true`), each entry once; and
the day it was last read (`checked`). One project per line, so a reader
can keep only those it wants while reading: about 25 MB of memory instead
of 1.4 GB for all of them at once.

It's read the way nixpkgs-update reads Repology, from its lists of
nixpkgs' projects, 200 to a request, and less often for what changes less
often. All of nixpkgs costs about 650 requests a day in two runs (at most
930), within Repology's [API rules](https://repology.org/api): at most one
request a second, under 1,000 a day. Each run:

1. **Outdated projects**: Repology's list of nixpkgs' outdated projects,
   all of it (about 70 requests), as nixpkgs-update reads it. Every
   outdated package's versions are at most 12 hours old.
2. **No longer outdated**: the projects that were outdated and aren't on
   the list now (updated in nixpkgs, mostly), read one by one (up to 250 a
   run).
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
nothing, the other sources' files included: the last digest stays, and
nixkeeper asks Repology itself for what it lacks.

### nixpkgs: `nixpkgs.json.gz`

[`data/nixpkgs.json.gz`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/nixpkgs.json.gz):
nixpkgs' own facts about its packages, for readers without Nix (such as
[nixkeeper-vulnerabilities](https://github.com/nixkeeper/digest-vulnerabilities),
which matches packages to advisories by registry name and version, and by
source repository and tag or commit):

```json
{"format": 1, "indexedAt": "...", "revision": "...",
 "evaluatedAt": "...", "evaluatedRevision": "...",
 "packages": {"python313Packages.requests": {"pname": "requests", "version": "2.32.5"},
              "ripgrep": {"pname": "ripgrep", "version": "15.1.0",
                          "src": {"gitRepoUrl": "https://github.com/BurntSushi/ripgrep.git",
                                  "tag": "15.1.0", "url": "..."}}, ...}}
```

Every attribute's `pname` and `version`, from the channel's package index
(read daily, `revision` its commit); and `src`, where its source comes from
(the fields it has of `gitRepoUrl`, `tag`, `rev`, `url`), for the
attributes the weekly evaluation above covers (outside the sets nixkeeper
compares with their own sources). A run that can't read the index keeps
the last.

### `meta.json`

[`data/meta.json`](https://raw.githubusercontent.com/nixkeeper/digest-versions/data/data/meta.json):
when each source was last read and how many packages it had (`releases`,
`typst`, `emacs`, `stackage`, `cran`, `sets`, `registries`), when nixpkgs' index was read and its
sources evaluated, with how many attributes and sources (`nixpkgs`);
how many attributes have a version from a source of their own, counted
from the files after each run (`coverage`: `attributes`; `sets`, in a set
updated by its own scripts or not versioned; `forges`, with a forge check,
by forge; `pins`, pinned to a GitHub commit and compared with its
default branch; `registries`, with a version from its registry, by
registry; `noSource`, with no source in the evaluation; `repologyOnly`, the
rest, and `repologyOnlySources`, their distinct sources); and
for Repology, when the outdated projects were last read (`outdatedAt`), how
many there are, where the weekly rotation is (`rotation.next`, and
`rotation.lapAt`, when it last went through all of them), the dropped
projects' attributes looked up again and found in another project
(`moved`: `asked`, `found`), how many projects there are, and how many
requests the run made.

The `data` branch is `main` plus one commit with the digest: each run
replaces it, so no history piles up.

## How it's kept up to date

The "Digest" workflow runs every 12 hours, started from outside GitHub at
04:15 and 16:15 UTC (nixkeeper's `start-runs`), each run done (in about 85
minutes, most of it GitHub and the other forges) before one of nixkeeper's
syncs; its own schedule (05:37 and 17:37, often hours late) is the
fallback, and stops when the digest is under 10 hours old. Each run reads
Repology (above), the package indexes and every forge repository; weekly,
it evaluates nixpkgs again for where packages come from.

## Running it

```bash
nix run . -- data
```

brings the digest in `data/` up to date (`-- data --full` reads all of
Repology's projects; `--if-older 10` only when the last run is over 10
hours old, so a run started twice reads once; `--sources-only` every source
but Repology, to try them without spending its daily allowance:
"sources_only" when starting the workflow by hand). `nix flake check` runs
the tests and lint, `nix fmt` formats.

## AI & automation disclosure

This digest's code and documentation are written with the assistance of an
AI (Claude, by Anthropic), with a person in the loop: nixkeeper's maintainer
decides what to build, and reviews and tests every change before it's
merged. The digest itself is automation: a scheduled workflow that reads
public sources and publishes what it finds. It never comments on, closes or
merges anything in nixpkgs.

## License

MIT
