"""How many of nixpkgs' attributes have an upstream version from a source
of their own, and how many only from Repology, counted after each run from
the published files (nixpkgs.json.gz, releases.json.gz,
registries.json.gz), for meta.json:

    "coverage": {"attributes": 151804, "sets": 77712,
                 "forges": {"forgejo": 325, "github": 34146, "gitlab": 1052},
                 "pins": 5111, "registries": {"crates.io": 241},
                 "noSource": 4353, "repologyOnly": 34216,
                 "repologyOnlySources": 22057}

"sets": in a set nixkeeper compares with the set's own source, or doesn't
version (releases.in_bulk_set); "forges": with a forge check (releases.py),
by forge; "pins": pinned to a GitHub commit, compared with its default
branch (releases.py's "pins"); "registries": with a version from its
registry (registries.py), by registry; "noSource": outside the sets, with no source
in the weekly evaluation (wrappers, aliases, failures); "repologyOnly": the
rest, and "repologyOnlySources" their distinct sources (a repository,
else the source's address), as several attributes share one."""

from . import packages, registries, releases


def count(directory):
    """The coverage counts of the files in directory, or None without
    nixpkgs.json.gz."""
    nixpkgs = (packages.read(directory) or {}).get("packages")
    if not nixpkgs:
        return None
    found = releases.read(directory) or {}
    forge_of = {a: "github" for a in found.get("packages") or {}}
    forge_of |= {a: p["forge"] for a, p in (found.get("elsewhere") or {}).items()}
    pinned = set(found.get("pins") or {})
    registry_of = {}
    for registry, part in (
        (registries.read(directory) or {}).get("registries") or {}
    ).items():
        known = part.get("packages") or {}
        registry_of |= {
            a: registry
            for a, name in (part.get("attrs") or {}).items()
            if name in known
        }
    sets, pins, no_source, alone = 0, 0, 0, set()
    forges, from_registry = {}, {}
    sources = set()
    for attr, p in nixpkgs.items():
        if releases.in_bulk_set(attr):
            sets += 1
        elif attr in forge_of:
            forges[forge_of[attr]] = forges.get(forge_of[attr], 0) + 1
        elif attr in pinned:
            pins += 1
        elif attr in registry_of:
            r = registry_of[attr]
            from_registry[r] = from_registry.get(r, 0) + 1
        elif not p.get("src"):
            no_source += 1
        else:
            alone.add(attr)
            src = p["src"]
            sources.add(src.get("gitRepoUrl") or src.get("url") or attr)
    return {
        "attributes": len(nixpkgs),
        "sets": sets,
        "forges": dict(sorted(forges.items())),
        "pins": pins,
        "registries": dict(sorted(from_registry.items())),
        "noSource": no_source,
        "repologyOnly": len(alone),
        "repologyOnlySources": len(sources),
    }
