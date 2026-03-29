"""Race definition registry.

The context object that preference schemes call set_race(), set_subrace(),
set_faction_race(), set_npc_race() on. Collects all assignments into
structured data used by the furrification engine.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field

from .models import RaceAssignment, Subrace

log = logging.getLogger(__name__)

# Available preference schemes
SCHEMES = {
    'all_races': 'furrifier.preferences.all_races',
    'cats_dogs': 'furrifier.preferences.cats_dogs',
    'legacy': 'furrifier.preferences.legacy',
    'user': 'furrifier.preferences.user',
}


class RaceDefContext:
    """Collects race assignments from a preference scheme.

    Preference modules call methods on this object to register
    race mappings, subraces, faction overrides, and NPC overrides.
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

    def set_race(self, vanilla_id: str, furry_id: str, furry_class: str) -> None:
        """Map a vanilla race to a furry race."""
        self.assignments[vanilla_id] = RaceAssignment(
            vanilla_id=vanilla_id,
            furry_id=furry_id,
            furry_class=furry_class,
        )

    def set_subrace(self, subrace_id: str, display_name: str,
                    vanilla_basis: str, furry_id: str, furry_class: str) -> None:
        """Define a subrace (e.g. Reachman derived from Breton)."""
        self.subraces[subrace_id] = Subrace(
            name=subrace_id,
            display_name=display_name,
            vanilla_basis=vanilla_basis,
            furry_id=furry_id,
            furry_class=furry_class,
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


def load_scheme(scheme_name: str) -> RaceDefContext:
    """Load a preference scheme by name and return the populated context."""
    if scheme_name not in SCHEMES:
        raise ValueError(f"Unknown scheme: {scheme_name!r}. "
                         f"Available: {', '.join(SCHEMES)}")

    ctx = RaceDefContext()
    module = importlib.import_module(SCHEMES[scheme_name])
    module.configure(ctx)

    log.info(f"Loaded scheme {scheme_name!r}: "
             f"{len(ctx.assignments)} race assignments, "
             f"{len(ctx.subraces)} subraces, "
             f"{len(ctx.faction_races)} faction overrides")
    return ctx
