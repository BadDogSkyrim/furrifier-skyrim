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

from .models import (
    Breed, HeadpartRule, LeveledNpcEntry, LeveledNpcGroup,
    RaceAssignment, Subrace,
)
from .util import hash_string

log = logging.getLogger(__name__)



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
        # Headpart-assignment rules per (race_or_breed, sex_name_or_None,
        # HeadpartType.name) → HeadpartRule. Missing key = unconstrained
        # default (probability=1.0, no whitelist). sex_name is 'Male',
        # 'Female', or None (applies to both). race_or_breed may be a
        # registered race EDID OR a breed name; breeds inherit from
        # their parent race when silent on a type. See decision #5 in
        # PLAN_FURRIFIER_BREEDS.md.
        self.headpart_rules: dict[tuple, HeadpartRule] = {}
        # leveled-list extension groups: ordered list, first-match wins
        # against the LVLN editor_id
        self.leveled_npc_groups: list[LeveledNpcGroup] = []
        # leveled-list editor-id substrings to skip entirely (e.g. faction-
        # specific lists like Thalmor where lore-bound races shouldn't
        # gain furry duplicates)
        self.leveled_npc_exclusions: list[str] = []
        # Breed registry — see PLAN_FURRIFIER_BREEDS.md.
        # breeds: name -> Breed; breeds_by_parent: parent_race_edid ->
        # ordered list (definition order matters for the deterministic roll).
        self.breeds: dict[str, Breed] = {}
        self.breeds_by_parent: dict[str, list[Breed]] = {}


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


    def set_breed(self, name: str, parent_race_edid: str,
                  probability: float = 0.0) -> None:
        """Register a breed under its parent race.

        Probabilities for any single parent must sum to ≤ 1.0; the
        remainder is the breed-less slice (NPC drawn from parent's
        unconstrained pool). Raises ValueError on overflow.
        """
        existing_total = sum(b.probability for b in
                             self.breeds_by_parent.get(parent_race_edid, []))
        if existing_total + probability > 1.0 + 1e-9:
            raise ValueError(
                f"breed {name!r}: total probability for parent race "
                f"{parent_race_edid!r} would exceed 1.0 "
                f"({existing_total + probability:.3f} = "
                f"{existing_total:.3f} existing + {probability:.3f} new)")
        breed = Breed(name=name, parent_race_edid=parent_race_edid,
                      probability=probability)
        self.breeds[name] = breed
        self.breeds_by_parent.setdefault(parent_race_edid, []).append(breed)


    def resolve_race_or_breed(self, name: str) -> tuple[str, Optional[Breed]]:
        """Resolve a name (from a scheme entry) to (engine_race_edid, breed).

        If `name` is a registered breed, returns its parent race EDID and
        the Breed object. Otherwise the name is assumed to be a race
        EDID and returned as-is with breed=None — race-existence
        validation happens later at session setup, when actual RACE
        records get loaded from the plugin set.
        """
        breed = self.breeds.get(name)
        if breed is not None:
            return breed.parent_race_edid, breed
        return name, None


    def roll_breed(self, npc_alias: str,
                   parent_race_edid: str) -> Optional[Breed]:
        """Hash-roll across the breeds defined for `parent_race_edid`.

        Returns the picked breed, or None if the roll lands in the
        breed-less slice (always the case for races with no breeds, or
        when the sum of breed probabilities is < 1.0 and the hash falls
        outside any slice). probability=0 breeds are never auto-picked.
        """
        breeds = self.breeds_by_parent.get(parent_race_edid)
        if not breeds:
            return None
        # 10000 buckets gives 0.0001 resolution — enough for the 0.01
        # granularity scheme authors will reasonably use.
        BUCKETS = 10000
        roll = hash_string(npc_alias, 7919, BUCKETS)
        cumulative = 0
        for breed in breeds:
            slice_width = int(round(breed.probability * BUCKETS))
            if slice_width == 0:
                continue
            if roll < cumulative + slice_width:
                return breed
            cumulative += slice_width
        return None


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


    def set_headpart_probability(self, furry_race_id: str,
                                 sex: Optional[str],
                                 hp_type_name: str, probability: float) -> None:
        """Register assignment probability for a (race, sex, headpart type).

        sex is 'Male', 'Female', or None (applies to both).
        hp_type_name is a HeadpartType enum name, e.g. 'EYEBROWS'.

        Backwards-compat shim around `set_headpart_rule` — sets a rule
        with the given probability and no headpart whitelist.
        """
        self.set_headpart_rule(furry_race_id, sex, hp_type_name,
                               probability=probability)


    def set_headpart_rule(self, race_or_breed: str,
                          sex: Optional[str],
                          hp_type_name: str,
                          probability: float = 1.0,
                          headpart_whitelist: tuple[str, ...] = (),
                          ) -> None:
        """Register a headpart rule for a (race-or-breed, sex, type).

        `race_or_breed` is a race EDID (e.g. 'BDDeerRace') or a breed
        name (e.g. 'WhiteTail'). `headpart_whitelist`, when non-empty,
        restricts the candidate pool at selection time to those
        EditorIDs.
        """
        self.headpart_rules[(race_or_breed, sex, hp_type_name)] = (
            HeadpartRule(probability=probability,
                         headpart_whitelist=tuple(headpart_whitelist)))


    def get_headpart_rule(self, race_or_breed: str, sex: Optional[str],
                          hp_type_name: str) -> HeadpartRule:
        """Look up a headpart rule with breed→parent inheritance.

        Resolution order:
        1. (breed, sex, type) — if `race_or_breed` is a registered breed
        2. (breed, None, type)
        3. (parent_race or race_or_breed, sex, type)
        4. (parent_race or race_or_breed, None, type)
        5. ('*', sex, type) — wildcard race
        6. ('*', None, type)
        7. default HeadpartRule()

        Decision #5 in PLAN_FURRIFIER_BREEDS.md: breed silence on a
        type means inherit from parent race.
        """
        breed = self.breeds.get(race_or_breed)
        if breed is not None:
            for sex_key in (sex, None):
                rule = self.headpart_rules.get(
                    (breed.name, sex_key, hp_type_name))
                if rule is not None:
                    return rule
            race_key = breed.parent_race_edid
        else:
            race_key = race_or_breed

        for outer_key in (race_key, '*'):
            for sex_key in (sex, None):
                rule = self.headpart_rules.get(
                    (outer_key, sex_key, hp_type_name))
                if rule is not None:
                    return rule
        return HeadpartRule()


    def get_headpart_probability(self, furry_race_id: str, sex: str,
                                 hp_type_name: str) -> float:
        """Look up probability with the same fallback chain as
        `get_headpart_rule`. Returns 1.0 when no rule applies."""
        return self.get_headpart_rule(
            furry_race_id, sex, hp_type_name).probability


_LEVELED_NPCS_KEYS = frozenset({'exclude_substrings', 'groups'})
_GROUP_KEYS = frozenset({'match_substrings', 'races'})
_RACE_RULE_KEYS = frozenset({'race', 'probability'})


def _parse_leveled_npcs(data: dict, ctx: 'RaceDefContext',
                        scheme_path: Path) -> None:
    """Parse the [leveled_npcs] section of a scheme.

    Emits warnings (via log.warning, surfaced in the run summary) for
    unknown keys, missing required fields, and the obsolete top-level
    ``races = [...]`` shape — common authoring mistakes that would
    otherwise silently produce zero leveled-list overrides.
    """
    section = data.get('leveled_npcs')
    if section is None:
        return
    if not isinstance(section, dict):
        log.warning(
            f"{scheme_path.name}: [leveled_npcs] is not a table; "
            f"ignoring leveled-NPC config")
        return

    for key in section:
        if key not in _LEVELED_NPCS_KEYS:
            if key == 'races':
                log.warning(
                    f"{scheme_path.name}: [leveled_npcs] uses obsolete "
                    f"top-level 'races =' — wrap in '[[leveled_npcs."
                    f"groups]]' (with optional match_substrings) to "
                    f"enable leveled-list extension")
            else:
                log.warning(
                    f"{scheme_path.name}: [leveled_npcs] has unknown "
                    f"key {key!r} (expected one of "
                    f"{sorted(_LEVELED_NPCS_KEYS)})")

    ctx.leveled_npc_exclusions = list(
        section.get('exclude_substrings', []))

    for i, group in enumerate(section.get('groups', [])):
        label = f"[leveled_npcs.groups][{i}]"
        if not isinstance(group, dict):
            log.warning(
                f"{scheme_path.name}: {label} is not a table; skipping")
            continue
        for key in group:
            if key not in _GROUP_KEYS:
                log.warning(
                    f"{scheme_path.name}: {label} has unknown key "
                    f"{key!r} (expected one of {sorted(_GROUP_KEYS)})")

        races: list[LeveledNpcEntry] = []
        for j, rule in enumerate(group.get('races', [])):
            rule_label = f"{label}.races[{j}]"
            if not isinstance(rule, dict):
                log.warning(
                    f"{scheme_path.name}: {rule_label} is not a "
                    f"table; skipping")
                continue
            for key in rule:
                if key not in _RACE_RULE_KEYS:
                    log.warning(
                        f"{scheme_path.name}: {rule_label} has unknown "
                        f"key {key!r} (expected one of "
                        f"{sorted(_RACE_RULE_KEYS)})")
            if 'race' not in rule:
                log.warning(
                    f"{scheme_path.name}: {rule_label} missing required "
                    f"'race' key; skipping")
                continue
            if 'probability' not in rule:
                log.warning(
                    f"{scheme_path.name}: {rule_label} missing required "
                    f"'probability' key; skipping")
                continue
            races.append(LeveledNpcEntry(
                race=str(rule['race']),
                probability=float(rule['probability'])))

        ctx.leveled_npc_groups.append(LeveledNpcGroup(
            match_substrings=list(group.get('match_substrings', [])),
            races=races,
        ))


def list_available_schemes() -> list[str]:
    """Names of scheme TOMLs discovered in the shipped `schemes/` folder.

    Returns a sorted list of lowercase stems so argparse's
    ``type=str.lower`` path matches files regardless of source case.
    Empty list if the directory can't be located — callers decide
    whether that's a hard error (load_scheme raises) or a no-op
    (argparse drops the choices constraint, GUI combo stays empty).

    Stems ending in ``_test`` are frozen test fixtures (filtered out
    of the kit by furrify_skyrim.spec) and are hidden from the CLI /
    GUI even in dev mode. `load_scheme` itself can still load them —
    they're only excluded from discovery.
    """
    schemes_dir = _find_resource_dir('schemes')
    if schemes_dir is None:
        return []
    stems = (p.stem.lower() for p in schemes_dir.glob('*.toml'))
    return sorted(s for s in stems if not s.endswith('_test'))


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
    for entry in data.get('headpart_probability', []):
        race = entry['race']
        sex = entry.get('sex')  # 'Male', 'Female', or absent
        for hp_type_name, value in entry.items():
            if hp_type_name in ('race', 'sex'):
                continue
            # Per-type value is either a flat probability (existing
            # format) or a structured table {probability=..., headpart=
            # [...]} for breed-style whitelisting (Phase 2).
            if isinstance(value, dict):
                ctx.set_headpart_rule(
                    race, sex, hp_type_name,
                    probability=float(value.get('probability', 1.0)),
                    headpart_whitelist=tuple(value.get('headpart', ())),
                )
            else:
                ctx.set_headpart_probability(
                    race, sex, hp_type_name, float(value))
    for entry in data.get('breeds', []):
        ctx.set_breed(
            name=entry['breed'],
            parent_race_edid=entry['race'],
            probability=float(entry.get('probability', 0.0)),
        )


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

    _parse_leveled_npcs(data, ctx, scheme_path)

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
