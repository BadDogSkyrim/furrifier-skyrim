"""Race definition registry.

The context object that preference schemes populate with race
assignments, subrace definitions, faction/NPC overrides, headpart
equivalents, and headpart labels.

Schemes are defined as TOML files in the ``schemes/`` folder next to
the furrify_skyrim executable (or at the project root in a dev
checkout). Users customize the tool by editing ``schemes/user.toml``
or creating their own scheme file alongside the shipped ones.
"""

from __future__ import annotations

import logging
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .models import RaceAssignment, Subrace

log = logging.getLogger(__name__)

# Canonical list of schemes shipped with furrifier. Used by argparse
# --choices and by the test suite. User-added scheme files aren't in
# this tuple; they'd be added here (or discovered dynamically) in a
# future pass.
SCHEMES = ('all_races', 'cats_dogs', 'legacy', 'user')


class RaceDefContext:
    """Collects race assignments from a preference scheme.

    The TOML loader calls methods on this object to register race
    mappings, subraces, faction overrides, NPC overrides, headpart
    equivalents, and headpart labels. Additional methods exist for
    vanilla setup (label conflicts, empty headparts, etc.) that the
    TOML scheme format does not currently expose.
    """


    def __init__(self):
        # vanilla EditorID -> RaceAssignment
        self.assignments: dict[str, RaceAssignment] = {}
        # subrace EditorID -> Subrace
        self.subraces: dict[str, Subrace] = {}
        # faction EditorID -> subrace/race EditorID
        self.faction_races: dict[str, str] = {}
        # NPC EditorID -> subrace/race EditorID
        self.npc_races: dict[str, str] = {}
        # headpart labels: headpart EditorID -> list of label strings
        self.headpart_labels: dict[str, list[str]] = {}
        # headpart equivalents: vanilla EditorID -> list of furry EditorIDs
        self.headpart_equivalents: dict[str, list[str]] = {}
        # label conflicts: set of frozenset({label1, label2})
        self.label_conflicts: set[frozenset] = set()
        # empty headparts: set of EditorIDs that represent "no headpart"
        self.empty_headparts: set[str] = set()


    def set_race(self, vanilla_id: str, furry_id: str) -> None:
        """Map a vanilla race to a furry race."""
        self.assignments[vanilla_id] = RaceAssignment(
            vanilla_id=vanilla_id,
            furry_id=furry_id,
        )


    def set_subrace(self, subrace_id: str, display_name: str,
                    vanilla_basis: str, furry_id: str) -> None:
        """Define a subrace (e.g. Reachman derived from Breton)."""
        self.subraces[subrace_id] = Subrace(
            name=subrace_id,
            display_name=display_name,
            vanilla_basis=vanilla_basis,
            furry_id=furry_id,
        )


    def set_faction_race(self, faction_id: str, race_id: str) -> None:
        """Force all members of a faction to a specific race."""
        self.faction_races[faction_id] = race_id


    def set_npc_race(self, npc_edid: str, race_id: str) -> None:
        """Force a specific NPC to a specific race."""
        self.npc_races[npc_edid] = race_id


    def set_tattoo_race(self, tattoo_str: str, race_id: str) -> None:
        """Assign NPCs with a specific tattoo to a race. Not yet implemented."""
        log.debug(f"SetTattooRace not implemented: {tattoo_str} -> {race_id}")


    def label_headpart(self, headpart_id: str, label: str) -> None:
        """Add a single label to a headpart."""
        self.headpart_labels.setdefault(headpart_id, []).append(label)


    def label_headpart_list(self, headpart_id: str, labels: str) -> None:
        """Add comma-separated labels to a headpart."""
        label_list = [l.strip() for l in labels.split(',') if l.strip()]
        self.headpart_labels.setdefault(headpart_id, []).extend(label_list)


    def label_conflict(self, label1: str, label2: str) -> None:
        """Register two labels as conflicting."""
        self.label_conflicts.add(frozenset({label1, label2}))


    def assign_headpart(self, vanilla_id: str, furry_id: str) -> None:
        """Register a 1:1 headpart equivalence."""
        self.headpart_equivalents.setdefault(vanilla_id, []).append(furry_id)


    def set_empty_headpart(self, headpart_id: str) -> None:
        """Mark a headpart as representing an empty slot."""
        self.empty_headparts.add(headpart_id)


def _find_resource_dir(name: str) -> Optional[Path]:
    """Locate a top-level resource directory (schemes/ or races/) in
    frozen or dev mode.

    Frozen (packaged exe): next to sys.executable.
    Dev checkout: walk up from this file looking for a sibling with the
    given name.
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent / name

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / name
        if candidate.is_dir():
            return candidate
    return None


def _apply_race_catalog(ctx: RaceDefContext, data: dict) -> None:
    """Merge one race catalog file's data into the context.

    Race catalog files contain headpart equivalents and labels that
    describe the furry headparts available in a furry-race mod. They
    are scheme-independent — every load picks them up regardless of
    which scheme was selected.
    """
    for h in data.get('headpart_equivalents', []):
        ctx.assign_headpart(h['vanilla'], h['furry'])
    for hp_id, labels in data.get('headpart_labels', {}).items():
        ctx.label_headpart_list(hp_id, labels)


def _load_race_catalogs(ctx: RaceDefContext) -> None:
    """Vacuum every ``races/*.toml`` file into the context.

    Missing ``races/`` directory is non-fatal: furrifier will use ESP
    fallback paths for any headpart data a race doesn't define. This
    lets users run against custom mods without writing race catalog
    files first.
    """
    races_dir = _find_resource_dir('races')
    if races_dir is None:
        log.debug("No races/ directory found; skipping race catalog load.")
        return

    files = sorted(races_dir.glob('*.toml'))
    if not files:
        log.debug(f"races/ directory found at {races_dir} but contains no .toml files.")
        return

    for path in files:
        with open(path, 'rb') as f:
            data = tomllib.load(f)
        _apply_race_catalog(ctx, data)
        log.debug(
            f"Loaded race catalog {path.name}: "
            f"{len(data.get('headpart_equivalents', []))} equivalents, "
            f"{len(data.get('headpart_labels', {}))} labels"
        )


def load_scheme(scheme_name: str) -> RaceDefContext:
    """Load a preference scheme by name and return the populated context.

    The scheme is read from ``schemes/<name>.toml``. All files in
    ``races/*.toml`` are then merged in as race catalog data
    (headpart equivalents and labels). See ``schemes/all_races.toml``
    and ``races/yas_races.toml`` for examples.
    """
    schemes_dir = _find_resource_dir('schemes')
    if schemes_dir is None:
        raise ValueError(
            f"Could not locate schemes/ directory. Expected it next to the "
            f"executable (packaged) or at the project root (dev)."
        )

    scheme_path = schemes_dir / f"{scheme_name}.toml"
    if not scheme_path.is_file():
        available = sorted(p.stem for p in schemes_dir.glob("*.toml"))
        raise ValueError(
            f"Unknown scheme: {scheme_name!r}. "
            f"Available in {schemes_dir}: {', '.join(available) or '(none)'}"
        )

    with open(scheme_path, 'rb') as f:
        data = tomllib.load(f)

    ctx = RaceDefContext()

    for r in data.get('races', []):
        ctx.set_race(r['vanilla'], r['furry'])

    for s in data.get('subraces', []):
        ctx.set_subrace(s['id'], s['name'], s['basis'], s['furry'])

    for faction_id, race_id in data.get('faction_races', {}).items():
        ctx.set_faction_race(faction_id, race_id)

    for npc_edid, race_id in data.get('npc_races', {}).items():
        ctx.set_npc_race(npc_edid, race_id)

    # Merge race catalog data from races/*.toml. This is scheme-
    # independent — every scheme picks up the same catalog data.
    _load_race_catalogs(ctx)

    log.info(f"Loaded scheme {scheme_name!r}: "
             f"{len(ctx.assignments)} race assignments, "
             f"{len(ctx.subraces)} subraces, "
             f"{len(ctx.faction_races)} faction overrides, "
             f"{sum(len(v) for v in ctx.headpart_equivalents.values())} headpart equivalents, "
             f"{len(ctx.headpart_labels)} labeled headparts")
    return ctx
