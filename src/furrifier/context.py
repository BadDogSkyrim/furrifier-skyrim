"""FurryContext -- bundles all state needed for furrification.

Instead of passing patch, ctx, races, headparts, tints, etc. as separate
parameters to every function, FurryContext holds them all and exposes
furrification methods directly.
"""

from __future__ import annotations

import logging
import struct
from typing import Optional

from esplib import Plugin, PluginSet, Record
from esplib.utils import FormID

from .models import Sex, HeadpartType, HeadpartInfo, Bodypart
from .race_defs import RaceDefContext
from .vanilla_setup import unalias
from .furry_load import is_npc_female, is_child_race
from .headparts import (
    load_npc_labels, find_similar_headpart, _should_assign,
    _PROBABILITY_GATED_TYPES,
)
from .util import hash_string, short_race_name
from .tints import choose_furry_tints

log = logging.getLogger(__name__)

# Bodypart flags that indicate armor needing furry race support
FURRIFIABLE_BODYPARTS = (
    Bodypart.HEAD | Bodypart.HAIR | Bodypart.HANDS |
    Bodypart.LONGHAIR | Bodypart.CIRCLET | Bodypart.SCHLONG
)

# Race EditorID variant suffixes, longest-match first.
_RACE_VARIANT_SUFFIXES = ('ChildVampire', 'Vampire', 'Child')


def _variant_suffix(race_edid: str) -> str:
    """Return the variant suffix on a race EditorID, or '' for adults.

    Used to keep vampire/child NPCs in their own family when extending
    leveled lists: a vampire source NPC must spawn a vampire furry
    duplicate, not an adult one.
    """
    for suffix in _RACE_VARIANT_SUFFIXES:
        if race_edid.endswith(suffix):
            return suffix
    return ''


def _strip_variant_suffix(race_edid: str) -> str:
    """Return the base (adult) race EditorID."""
    suffix = _variant_suffix(race_edid)
    return race_edid[:-len(suffix)] if suffix else race_edid


def _variant_names(base_race: str) -> tuple[str, ...]:
    return (base_race,) + tuple(
        base_race + s for s in _RACE_VARIANT_SUFFIXES)


class FurryContext:
    """All state needed to furrify NPCs, armor, and schlongs."""

    def __init__(self,
                 patch: Plugin,
                 ctx: RaceDefContext,
                 races: dict[str, Record],
                 all_headparts: dict[str, HeadpartInfo],
                 race_headparts: dict,
                 race_tints: dict,
                 plugin_set: PluginSet = None,
                 max_tint_layers: int = 200):
        self.patch = patch
        self.ctx = ctx
        self.races = races
        self.all_headparts = all_headparts
        self.race_headparts = race_headparts
        self.race_tints = race_tints
        self.plugin_set = plugin_set
        self.max_tint_layers = max_tint_layers
        # Statistics (populated during furrification)
        self.stats_race_counts: dict[str, int] = {}   # furry_race_id -> count
        self.stats_hair_male: dict[str, int] = {}     # headpart_edid -> count
        self.stats_hair_female: dict[str, int] = {}   # headpart_edid -> count


    def _copy_record(self, record, source_plugin=None):
        """Copy a record into the patch, with string fallback resolution."""
        return self.patch.copy_record(record, source_plugin)

    # -- NPC furrification --

    def determine_npc_sex(self, npc: Record, race: Optional[Record]) -> Sex:
        """Determine the NPC's Sex enum from ACBS flags and race."""
        female = is_npc_female(npc)
        child = is_child_race(race) if race is not None else False
        return Sex.from_flags(female=female, child=child)

    def _add_headpart_pnam(self, record: Record, hp: HeadpartInfo) -> None:
        """Add a PNAM subrecord for a headpart."""
        norm_fid = hp.record.normalize_form_id(hp.record.form_id)
        sr = record.add_subrecord('PNAM', b'\x00\x00\x00\x00')
        self.patch.write_form_id(sr, 0, norm_fid)


    def determine_npc_race(self, npc: Record,
                           ) -> Optional[tuple[str, str, str]]:
        """Determine vanilla, assigned, and furry race for an NPC.

        Returns (original_race_id, assigned_race_id, furry_race_id) or None
        if the NPC's race isn't furrifiable.
        """
        rnam = npc.get_subrecord('RNAM')
        if rnam is None:
            return None
        race_fid = npc.normalize_form_id(rnam.get_form_id()).value

        original_race_id = None
        for edid, rec in self.races.items():
            norm = rec.normalize_form_id(rec.form_id).value
            if norm == race_fid:
                original_race_id = edid
                break

        if original_race_id is None:
            return None

        assigned_race_id = original_race_id

        npc_edid = unalias(npc.editor_id or '')
        if npc_edid in self.ctx.npc_races:
            assigned_race_id = self.ctx.npc_races[npc_edid]

        # Check faction-based race assignment (only if no NPC override)
        elif npc.plugin is not None and self.plugin_set is not None:
            for sr in npc.get_subrecords('SNAM'):
                fact = self.plugin_set.resolve_form_id(
                    sr.get_form_id(), npc.plugin)
                if fact is None:
                    continue
                race_id = self.ctx.faction_races.get(fact.editor_id)
                if race_id is None:
                    continue
                # Only apply if the NPC's vanilla race matches the
                # subrace's basis (e.g. don't assign NordRaceChild
                # to a subrace based on NordRace)
                subrace = self.ctx.subraces.get(race_id)
                if subrace and subrace.vanilla_basis != original_race_id:
                    continue
                assigned_race_id = race_id
                break

        if assigned_race_id in self.ctx.assignments:
            furry_race_id = self.ctx.assignments[assigned_race_id].furry_id
        elif assigned_race_id in self.ctx.subraces:
            furry_race_id = self.ctx.subraces[assigned_race_id].furry_id
        else:
            return None

        return (original_race_id, assigned_race_id, furry_race_id)

    def furrify_npc(self, npc: Record,
                    override_furry_race: Optional[str] = None,
                    ) -> Optional[Record]:
        """Furrify a single NPC.

        Creates an override in the patch plugin with furry race, headparts,
        and tint layers. Returns the patched record, or None if skipped.

        If ``override_furry_race`` is set, the NPC is forced to that furry
        race (RNAM is rewritten to point at it) regardless of normal
        scheme assignments. Used by the leveled-list extension to assign
        a specific furry race to a duplicated NPC.
        """
        # Skip chargen presets
        acbs = npc['ACBS']
        if acbs and acbs['flags'].IsCharGenFacePreset:
            return None

        if override_furry_race is not None:
            rnam = npc.get_subrecord('RNAM')
            if rnam is None:
                return None
            race_fid = npc.normalize_form_id(rnam.get_form_id()).value
            original_race_id = None
            for edid, rec in self.races.items():
                if rec.normalize_form_id(rec.form_id).value == race_fid:
                    original_race_id = edid
                    break
            if original_race_id is None:
                return None
            # Treat the override race as both the assigned race (so RNAM
            # gets rewritten) and the visual furry race.
            assigned_race_id = override_furry_race
            furry_race_id = override_furry_race
        else:
            race_result = self.determine_npc_race(npc)
            if race_result is None:
                return None
            original_race_id, assigned_race_id, furry_race_id = race_result
        race_record = self.races.get(original_race_id)
        npc_sex = self.determine_npc_sex(npc, race_record)
        npc_alias = unalias(npc.editor_id or str(npc.form_id))

        log.debug(f"Furrifying {npc_alias}: {original_race_id} -> {furry_race_id}")

        patched = self._copy_record(npc)

        # Only change RNAM for subraces (e.g. Breton -> Reachman).
        # Normal races (e.g. Nord) are furrified at the race record level,
        # so the NPC keeps its original RNAM.
        # For subraces, point RNAM at the created subrace record.
        if assigned_race_id != original_race_id:
            subrace_rec = self.races.get(assigned_race_id)
            if subrace_rec is not None:
                rnam_sr = patched.get_subrecord('RNAM')
                # Normalize to load-order space first. Patch-created
                # subrace records carry the local sentinel and round-trip
                # safely; loaded race records (used by leveled-list
                # extension) need real normalization to avoid having
                # their master-list index misread as a load-order index.
                norm_fid = subrace_rec.normalize_form_id(
                    subrace_rec.form_id)
                self.patch.write_form_id(rnam_sr, 0, norm_fid)

        # Remove vanilla character customization
        patched.remove_subrecords('FTST')
        patched.remove_subrecords('QNAM')
        patched.remove_subrecords('NAM9')
        patched.remove_subrecords('TINI')
        patched.remove_subrecords('TINC')
        patched.remove_subrecords('TIAS')
        patched.remove_subrecords('TINV')

        # Zero out face part indices — vanilla indices may not be valid
        # for the furry race's face part arrays. TODO: map to furry presets.
        nama = patched.get_subrecord('NAMA')
        if nama:
            nama.data = bytearray(len(nama.data))
            nama.modified = True

        # Load NPC labels for headpart matching
        labels = load_npc_labels(npc, self.ctx)

        # Replace headparts
        old_headpart_srs = npc.get_subrecords('PNAM')
        patched.remove_subrecords('PNAM')

        assigned_types: set[HeadpartType] = set()
        for old_sr in old_headpart_srs:
            old_fid = old_sr.get_uint32()
            old_obj_id = old_fid & 0x00FFFFFF
            old_hp = None
            for hp in self.all_headparts.values():
                if hp.record and (hp.record.form_id.value & 0x00FFFFFF) == old_obj_id:
                    old_hp = hp
                    break
            if old_hp is None:
                continue

            new_hp = find_similar_headpart(
                old_hp, npc_alias, npc_sex, labels,
                furry_race_id, self.race_headparts, self.all_headparts,
                self.ctx,
            )
            if new_hp and new_hp.record:
                self._add_headpart_pnam(patched, new_hp)
                assigned_types.add(new_hp.hp_type)

        # Add probability-gated headparts that the vanilla record didn't
        # include. Needed for ungulates — most vanilla NPCs don't carry
        # an EYEBROWS PNAM, so without this step minos/deer fall back to
        # the race's default headpart (a single "steer horns") for every
        # NPC that wasn't already given a brow.
        sex_key = int(npc_sex)
        for hp_type in _PROBABILITY_GATED_TYPES:
            if hp_type in assigned_types:
                continue
            if not _should_assign(npc_alias, furry_race_id, npc_sex,
                                  hp_type, self.ctx):
                continue
            candidates = self.race_headparts.get(
                (hp_type, sex_key, furry_race_id), set())
            if not candidates:
                continue
            candidate_list = sorted(candidates)
            idx = hash_string(npc_alias, 619 + int(hp_type),
                              len(candidate_list))
            hp = self.all_headparts.get(candidate_list[idx])
            if hp and hp.record:
                self._add_headpart_pnam(patched, hp)

        # Extract vanilla NPC tint classes for decoration layer preservation
        npc_tint_classes = self._extract_npc_tint_classes(
            npc, original_race_id, npc_sex)

        # Apply furry tint layers
        tint_choices = choose_furry_tints(
            npc_alias, npc_sex, furry_race_id,
            npc_tint_classes, self.race_tints, self.max_tint_layers,
        )

        skin_tone_color = None
        skin_tone_intensity = 0.0
        for choice in tint_choices:
            # Resolve TINC FormID to inline RGBA color
            color_rgba = self._resolve_color(choice.tinc)

            patched.add_subrecord('TINI', struct.pack('<H', choice.tini))
            patched.add_subrecord('TINC', struct.pack('<BBBB',
                                 color_rgba[0], color_rgba[1],
                                 color_rgba[2], color_rgba[3]))
            patched.add_subrecord('TINV', struct.pack('<I', round(choice.tinv * 100)))
            patched.add_subrecord('TIAS', struct.pack('<H', choice.tias))

            # Track skin tone for QNAM calculation
            if skin_tone_color is None:
                skin_tone_color = color_rgba
                skin_tone_intensity = round(choice.tinv * 100) / 100.0

        # Calculate QNAM from skin tone tint
        if skin_tone_color:
            self._apply_qnam_from_color(patched, skin_tone_color,
                                        skin_tone_intensity)

        # Track statistics
        self.stats_race_counts[furry_race_id] = \
            self.stats_race_counts.get(furry_race_id, 0) + 1
        for sr in patched.get_subrecords('PNAM'):
            hp_obj = sr.get_uint32() & 0x00FFFFFF
            for hp in self.all_headparts.values():
                if hp.record and (hp.record.form_id.value & 0x00FFFFFF) == hp_obj:
                    if hp.hp_type == HeadpartType.HAIR:
                        is_female = npc_sex in (Sex.FEMALE_ADULT, Sex.FEMALE_CHILD)
                        hair_dict = self.stats_hair_female if is_female \
                            else self.stats_hair_male
                        hair_dict[hp.editor_id] = \
                            hair_dict.get(hp.editor_id, 0) + 1
                    break

        return patched


    def _extract_npc_tint_classes(self, npc: Record,
                                  vanilla_race_id: str,
                                  npc_sex: Sex) -> set[str]:
        """Get the tint class names the vanilla NPC already has."""
        classes = set()
        race_key = (vanilla_race_id, npc_sex)
        race_data = self.race_tints.get(race_key)
        if race_data is None:
            return classes

        # Build TINI → class name lookup from the race tint data
        tini_to_class = {}
        for class_name, assets in race_data.classes.items():
            for asset in assets:
                tini_to_class[asset.index] = class_name

        # Look up each of the NPC's TINI values, but only count layers
        # that have non-zero TINV (intensity). TINV=0 means unused.
        subs = npc.subrecords
        for i, sr in enumerate(subs):
            if sr.signature != 'TINI':
                continue
            tini = struct.unpack('<H', sr.data[:2])[0]
            # Find the TINV that follows this TINI
            tinv = 0
            for j in range(i + 1, min(i + 4, len(subs))):
                if subs[j].signature == 'TINV':
                    tinv = struct.unpack('<I', subs[j].data[:4])[0]
                    break
                if subs[j].signature == 'TINI':
                    break
            if tinv > 0:
                class_name = tini_to_class.get(tini)
                if class_name:
                    classes.add(class_name)

        return classes


    def _resolve_color(self, tinc_fid: int) -> tuple[int, int, int, int]:
        """Resolve a TINC FormID (pointing to a CLFM record) to RGBA.

        Returns (R, G, B, A) as 0-255 values.
        """
        obj_id = tinc_fid & 0x00FFFFFF
        for plugin in self.plugin_set:
            for rec in plugin.get_records_by_signature('CLFM'):
                if (rec.form_id.value & 0x00FFFFFF) == obj_id:
                    cnam = rec.get_subrecord('CNAM')
                    if cnam and cnam.size >= 4:
                        return (cnam.data[0], cnam.data[1],
                                cnam.data[2], cnam.data[3])
                    elif cnam and cnam.size >= 3:
                        return (cnam.data[0], cnam.data[1],
                                cnam.data[2], 0)
        return (255, 255, 255, 0)


    def _apply_qnam_from_color(self, record: Record,
                                color: tuple[int, int, int, int],
                                intensity: float) -> None:
        """Calculate and apply QNAM from resolved color and intensity.

        QNAM is a lerp from neutral gray (127) to the skin tone color,
        with intensity as the interpolation factor. This matches CK
        behavior: TINV=0 gives neutral gray (no tint effect), TINV=1
        gives the full color.
        """
        qr = round(127 + (color[0] - 127) * intensity)
        qg = round(127 + (color[1] - 127) * intensity)
        qb = round(127 + (color[2] - 127) * intensity)

        record.add_subrecord('QNAM', struct.pack('<fff',
                             qr / 255.0, qg / 255.0, qb / 255.0))

    def furrify_all_npcs(self, plugins) -> int:
        """Furrify all NPCs across the load order. Returns count.

        Only processes the winning override of each NPC (last in load
        order). Skips NPCs that have already been overridden by plugins
        loaded after the ones we're processing.
        """
        # Build a map of FormID -> winning record across all plugins.
        # Last occurrence wins (plugins are in load order).
        winning: dict[int, Record] = {}
        for plugin in plugins:
            for npc in plugin.get_records_by_signature('NPC_'):
                obj_id = npc.form_id.value & 0x00FFFFFF
                winning[obj_id] = npc

        count = 0
        processed = 0
        total = len(winning)
        for obj_id, npc in winning.items():
            processed += 1
            if (processed % 500) == 0:
                log.debug(f"  NPCs: {processed}/{total}")
            result = self.furrify_npc(npc)
            if result is not None:
                count += 1

        log.debug(f"Total NPCs furrified: {count}")
        return count

    # -- Leveled NPC list extension --

    def extend_leveled_npcs(self, plugins) -> tuple[int, int]:
        """Extend humanoid LVLN records with furry NPC duplicates.

        For each LVLO entry whose source NPC has a furrifiable race
        (i.e. would be processed by furrify_npc), roll once per
        configured target furry race. On hit, duplicate the source NPC,
        assign it to the target race, run furrification, and append a
        new LVLO entry to the LVLN preserving the source entry's level
        and count. The same (source NPC, target race) pair generates a
        single shared duplicate even if it hits in multiple lists.

        Returns (npcs_created, lists_extended).
        """
        groups = list(self.ctx.leveled_npc_groups)
        if not groups:
            return (0, 0)

        # Strip vampire/child suffixes from each rule's target so we can
        # re-append the suffix that matches the source NPC's variant.
        # User-facing convention: specify the BASE adult race name.
        # Pre-compute (rule, base_race) for each group, dropping rules
        # whose target race isn't loaded.
        active_by_group: list[list[tuple]] = []
        for group in groups:
            active_rules: list[tuple] = []
            for rule in group.races:
                base = _strip_variant_suffix(rule.race)
                if any(v in self.races for v in _variant_names(base)):
                    active_rules.append((rule, base))
                else:
                    log.warning(
                        f"Leveled NPC race {rule.race!r} is not loaded; "
                        f"skipping rule")
            active_by_group.append(active_rules)
        if not any(active_by_group):
            return (0, 0)

        # Cache of created duplicates: (src_obj_id, furry_race) -> Record
        duplicates: dict[tuple[int, str], Record] = {}
        lists_extended = 0

        # Build NPC obj_id -> winning record lookup once
        npc_by_obj: dict[int, Record] = {}
        for plugin in plugins:
            for npc in plugin.get_records_by_signature('NPC_'):
                npc_by_obj[npc.form_id.value & 0x00FFFFFF] = npc

        # Walk LVLN winning overrides
        winning_lvln: dict[int, Record] = {}
        for plugin in plugins:
            for lvln in plugin.get_records_by_signature('LVLN'):
                winning_lvln[lvln.form_id.value & 0x00FFFFFF] = lvln

        exclusions = tuple(self.ctx.leveled_npc_exclusions)

        for lvln in winning_lvln.values():
            lvln_eid = lvln.editor_id or ''
            if any(s in lvln_eid for s in exclusions):
                continue

            # First-match-wins: pick the first group whose match_substrings
            # (case-insensitive substring) hits the LVLN editor_id, or a
            # group with no match_substrings (catch-all).
            active_rules = []
            for group, rules in zip(groups, active_by_group):
                if group.matches(lvln_eid):
                    active_rules = rules
                    break
            if not active_rules:
                continue

            new_entries: list[tuple[int, int, FormID]] = []
            # Dedupe: a single source NPC may appear multiple times in
            # one LVLN (at different levels); add at most one furry
            # duplicate per (src, target_race) pair per list.
            added_in_this_list: set[tuple[int, str]] = set()
            for sr in lvln.get_subrecords('LVLO'):
                if sr.size < 12:
                    continue
                level = struct.unpack_from('<H', sr.data, 0)[0]
                count = struct.unpack_from('<H', sr.data, 8)[0]
                ref_norm = lvln.normalize_form_id(
                    sr.get_form_id(4)).value
                ref_obj = ref_norm & 0x00FFFFFF
                src_npc = npc_by_obj.get(ref_obj)
                if src_npc is None:
                    continue
                src_race = self.determine_npc_race(src_npc)
                if src_race is None:
                    continue
                source_race_id = src_race[0]
                variant_suffix = _variant_suffix(source_race_id)

                src_alias = unalias(
                    src_npc.editor_id or str(src_npc.form_id))
                for rule, base_race in active_rules:
                    target_race = base_race + variant_suffix
                    if target_race not in self.races:
                        continue  # variant not defined for this family

                    if (ref_obj, target_race) in added_in_this_list:
                        continue

                    decision_key = (
                        f"{lvln.editor_id or ''}:{src_alias}:{base_race}")
                    threshold = int(rule.probability * 1000)
                    if hash_string(decision_key, 7831, 1000) >= threshold:
                        continue

                    cache_key = (ref_obj, target_race)
                    dup = duplicates.get(cache_key)
                    if dup is None:
                        dup = self._create_leveled_duplicate(
                            src_npc, target_race)
                        if dup is None:
                            continue
                        duplicates[cache_key] = dup

                    dup_norm = dup.normalize_form_id(dup.form_id)
                    new_entries.append((level, count, dup_norm))
                    added_in_this_list.add((ref_obj, target_race))

            if not new_entries:
                continue

            patched = self._copy_record(lvln)
            for level, count, ref_fid in new_entries:
                entry_data = struct.pack(
                    '<HHIHH', level, 0, 0, count, 0)
                sr = patched.add_subrecord('LVLO', entry_data)
                self.patch.write_form_id(sr, 4, ref_fid)

            llct = patched.get_subrecord('LLCT')
            if llct is not None and llct.size >= 1:
                new_count = sum(
                    1 for s in patched.subrecords if s.signature == 'LVLO')
                llct.data = bytearray([min(new_count, 255)])
                llct.modified = True

            lists_extended += 1

        log.debug(
            f"Leveled list extension: {len(duplicates)} NPCs, "
            f"{lists_extended} lists")
        return (len(duplicates), lists_extended)


    def _create_leveled_duplicate(self, src_npc: Record,
                                  furry_race: str) -> Optional[Record]:
        """Create a furrified duplicate of an NPC for leveled-list use.

        Furrifies src_npc — which copies it into the patch as an override
        — then promotes that override into a brand-new NPC by giving it a
        fresh FormID and renaming it to ``YAS_<src_edid>_<furry_race>``.
        """
        patched = self.furrify_npc(src_npc, override_furry_race=furry_race)
        if patched is None:
            return None

        patched.form_id = self.patch.get_next_form_id()
        self.patch._new_records.append(patched)

        src_edid = src_npc.editor_id or f"NPC{src_npc.form_id.value:08X}"
        new_edid = f"YAS_{src_edid}_{short_race_name(furry_race)}"
        edid_sr = patched.get_subrecord('EDID')
        if edid_sr is not None:
            edid_sr.data = bytearray((new_edid + '\x00').encode('cp1252'))
            edid_sr.modified = True

        return patched

    # -- Race furrification --

    # Single-value subrecords copied from the furry race (all FormIDs)
    _RACE_COPY_SIGS = ('WNAM', 'RNAM')

    # Subrecord sigs that make up Head Data (between NAM0 markers)
    _HEAD_DATA_SIGS = frozenset({
        'NAM0', 'MNAM', 'FNAM', 'HEAD', 'MPAI', 'MPAV', 'INDX', 'MODL',
        'RPRM', 'RPRF', 'AHCM', 'AHCF', 'FTSM', 'FTSF',
        'DFTM', 'DFTF', 'TINI', 'TINT', 'TINP', 'TIND',
        'TINC', 'TINV', 'TIRS',
    })

    # Head Data sigs that contain a FormID (all are exactly 4 bytes)
    _HEAD_FORMID_SIGS = frozenset({
        'HEAD', 'RPRM', 'RPRF', 'AHCM', 'AHCF',
        'FTSM', 'FTSF', 'DFTM', 'DFTF', 'MPAI', 'TIND', 'TINC',
    })


    def furrify_race(self, vanilla_race: Record,
                     furry_race: Record,
                     target: Record = None) -> Record:
        """Furrify a vanilla race by copying key subrecords from the furry race.

        Copies WNAM (skin), RNAM (armor race), and the entire Head Data
        section (head parts, tint masks, presets) from the furry race.
        FormIDs are remapped to the patch's master list.

        If target is provided, applies changes to it directly (for
        subrace records already in the patch). Otherwise creates an
        override via copy_record.

        Returns the patched race record.
        """
        patched = target or self._copy_record(vanilla_race)
        furry_plugin = furry_race.plugin

        # Copy simple FormID subrecords (WNAM, RNAM)
        for sig in self._RACE_COPY_SIGS:
            src_sr = furry_race.get_subrecord(sig)
            if src_sr is not None and src_sr.size == 4:
                norm_fid = furry_race.normalize_form_id(src_sr.get_form_id())
                dst_sr = patched.get_subrecord(sig)
                if dst_sr is None:
                    dst_sr = patched.add_subrecord(sig, b'\x00\x00\x00\x00')
                self.patch.write_form_id(dst_sr, 0, norm_fid)

        # Replace Head Data: remove vanilla head data, insert furry head data
        self._replace_head_data(patched, furry_race, furry_plugin)

        log.debug(f"Furrified race {vanilla_race.editor_id} "
                 f"from {furry_race.editor_id}")
        return patched


    def _replace_head_data(self, patched: Record, furry_race: Record,
                           furry_plugin) -> None:
        """Replace the Head Data section on a patched race record.

        Removes all head data subrecords from the patched record and
        inserts copies from the furry race, remapping FormIDs.
        """
        # Find and remove vanilla head data
        head_start = None
        head_end = None
        for i, sr in enumerate(patched.subrecords):
            if sr.signature == 'NAM0' and head_start is None:
                head_start = i
            if head_start is not None and sr.signature in self._HEAD_DATA_SIGS:
                head_end = i + 1

        if head_start is not None and head_end is not None:
            del patched.subrecords[head_start:head_end]
        else:
            head_start = len(patched.subrecords)

        # Extract furry race's head data
        furry_head = []
        in_head = False
        for sr in furry_race.subrecords:
            if sr.signature == 'NAM0' and not in_head:
                in_head = True
            if in_head and sr.signature in self._HEAD_DATA_SIGS:
                furry_head.append(sr)
            elif in_head and sr.signature not in self._HEAD_DATA_SIGS:
                # Some non-head-data sigs can appear between tint entries
                # (like TINC/TINV between TINI groups) — keep going
                pass

        # Copy with FormID remapping and insert
        from esplib.record import SubRecord
        for sr in furry_head:
            new_sr = SubRecord(sr.signature, bytes(sr.data))
            if sr.signature in self._HEAD_FORMID_SIGS and sr.size == 4:
                norm_fid = furry_race.normalize_form_id(sr.get_form_id())
                self.patch.write_form_id(new_sr, 0, norm_fid)
            patched.subrecords.insert(head_start, new_sr)
            head_start += 1

        patched.modified = True


    def furrify_all_races(self) -> int:
        """Furrify all vanilla races that have furry assignments.

        Also creates subrace records: copies the vanilla basis race,
        then furrifies the copy with the subrace's furry appearance.

        Returns count of races furrified.
        """
        count = 0

        # Furrify normal race assignments (e.g. NordRace -> YASLykaiosRace)
        for assignment in self.ctx.assignments.values():
            vanilla_rec = self.races.get(assignment.vanilla_id)
            furry_rec = self.races.get(assignment.furry_id)
            if vanilla_rec is None or furry_rec is None:
                continue
            patched = self.furrify_race(vanilla_rec, furry_rec)
            count += 1

            # Snow Elves show "High Elf" name in vanilla. Rename to prevent confusion.
            if assignment.vanilla_id == 'SnowElfRace':
                full_sr = patched.get_subrecord('FULL')
                if full_sr is not None:
                    full_sr.data = bytearray(b'Snow Elf\x00')
                    full_sr.modified = True

        # Create and furrify subrace records
        # (e.g. copy BretonRace -> YASReachmanRace, furrify with YASKonoiRace)
        for subrace in self.ctx.subraces.values():
            basis_rec = self.races.get(subrace.vanilla_basis)
            furry_rec = self.races.get(subrace.furry_id)
            if basis_rec is None or furry_rec is None:
                continue

            # Create a new race record as a copy of the vanilla basis.
            # copy_record handles delocalization (FULL, DESC) and masters.
            new_race = self._copy_record(basis_rec)

            # Assign a fresh FormID (copy_record gave it the basis's FormID)
            new_race.form_id = self.patch.get_next_form_id()
            self.patch._new_records.append(new_race)

            # Set EDID and FULL to the subrace identity
            edid_sr = new_race.get_subrecord('EDID')
            if edid_sr is not None:
                edid_sr.data = (subrace.name + '\x00').encode('cp1252')
                edid_sr.modified = True

            full_sr = new_race.get_subrecord('FULL')
            if full_sr is not None:
                full_sr.data = bytearray(
                    subrace.display_name.encode('cp1252') + b'\x00')
                full_sr.modified = True

            # Furrify with the furry race's appearance
            self.furrify_race(new_race, furry_rec, target=new_race)

            # Store so RNAM assignment can find it
            self.races[subrace.name] = new_race
            count += 1

        log.debug(f"Furrified {count} races")
        return count

    # -- Race preset furrification --

    def furrify_race_presets(self, plugins) -> int:
        """Copy furry race chargen presets and repoint them at furrified races.

        Race presets are NPC_ records referenced by RPRM (male) and RPRF
        (female) subrecords in the RACE record's Head Data. After
        furrify_all_races() copies the furry race's head data to the
        vanilla race, the presets still point at NPCs whose RNAM is the
        furry race. This method:
        1. For each furrified race in the patch, reads its preset FormIDs
        2. Resolves them to NPC_ records
        3. Copies each as a new record with RNAM set to the furrified race
        4. Replaces the preset FormIDs in the race record

        Returns count of preset NPC records created.
        """
        from esplib.record import SubRecord

        # Build NPC obj_id -> Record lookup for preset resolution
        npc_by_obj: dict[int, Record] = {}
        for plugin in plugins:
            for rec in plugin.get_records_by_signature('NPC_'):
                obj_id = rec.form_id.value & 0x00FFFFFF
                npc_by_obj[obj_id] = rec  # last wins

        count = 0

        for assignment in self.ctx.assignments.values():
            vanilla_rec = self.races.get(assignment.vanilla_id)
            furry_rec = self.races.get(assignment.furry_id)
            if vanilla_rec is None or furry_rec is None:
                continue

            # Find the furrified race in the patch
            furrified_rec = None
            for rec in self.patch.get_records_by_signature('RACE'):
                if rec.editor_id == assignment.vanilla_id:
                    furrified_rec = rec
                    break
            if furrified_rec is None:
                continue

            # Process male (RPRM) and female (RPRF) presets
            for preset_sig in ('RPRM', 'RPRF'):
                old_srs = furrified_rec.get_subrecords(preset_sig)
                if not old_srs:
                    continue

                new_preset_fids = []
                for sr in old_srs:
                    preset_obj = sr.get_form_id().object_index
                    preset_npc = npc_by_obj.get(preset_obj)
                    if preset_npc is None:
                        continue

                    # Copy preset NPC as a new record in the patch
                    new_preset = self._copy_record(preset_npc)
                    new_preset.form_id = self.patch.get_next_form_id()
                    self.patch._new_records.append(new_preset)

                    # Set EDID
                    old_edid = preset_npc.editor_id or 'Preset'
                    new_edid = f"{old_edid}_{assignment.vanilla_id}"
                    edid_sr = new_preset.get_subrecord('EDID')
                    if edid_sr is not None:
                        edid_sr.data = (new_edid + '\x00').encode('cp1252')
                        edid_sr.modified = True

                    # Set RNAM to the furrified vanilla race. Use the
                    # vanilla record's normalized FormID — furrified_rec's
                    # form_id is in patch master-list space, not load-order.
                    rnam_sr = new_preset.get_subrecord('RNAM')
                    if rnam_sr is not None:
                        vanilla_norm = vanilla_rec.normalize_form_id(
                            vanilla_rec.form_id)
                        self.patch.write_form_id(rnam_sr, 0, vanilla_norm)

                    new_preset_fids.append(new_preset.form_id)
                    count += 1

                # Replace preset subrecords in the furrified race.
                # Find the insertion point before removing, so new
                # subrecords go in the same position (not at the end).
                insert_idx = None
                for idx, sr in enumerate(furrified_rec.subrecords):
                    if sr.signature == preset_sig:
                        insert_idx = idx
                        break
                furrified_rec.remove_subrecords(preset_sig)
                if insert_idx is None:
                    marker = 'MNAM' if preset_sig == 'RPRM' else 'FNAM'
                    in_section = False
                    for idx, sr in enumerate(furrified_rec.subrecords):
                        if sr.signature == marker:
                            in_section = True
                        if in_section and sr.signature == 'MPAV':
                            insert_idx = idx + 1
                    if insert_idx is None:
                        insert_idx = len(furrified_rec.subrecords)
                for i, fid in enumerate(new_preset_fids):
                    new_sr = furrified_rec.insert_subrecord(
                        insert_idx + i, preset_sig, b'\x00\x00\x00\x00')
                    self.patch.write_form_id(new_sr, 0, fid)
                furrified_rec.modified = True

        log.debug(f"Created {count} race preset NPC records")
        return count

    # -- Headpart FormList furrification --

    def furrify_all_headpart_lists(self, plugins) -> int:
        """Update headpart race FormLists for furrified races.

        For each HDPT record in the load order:
        - Its RNAM subrecord points to a FLST of valid races.
        - If the FLST contains a furrified vanilla race, remove it.
        - If the FLST contains a furry race, add all furrified vanilla
          races that map to that furry race.

        This ensures that chargen shows the correct headparts for
        furrified races.

        Returns count of FLSTs modified.
        """
        from esplib import flst_forms, flst_add, flst_contains
        from esplib.record import SubRecord

        # Build lookup maps using normalized (load-order) FormIDs.
        # All FormIDs are in the same space so comparisons just work.

        # vanilla_fid -> furry_fid (furrified vanilla races to remove)
        vanilla_to_furry: dict[int, int] = {}
        # furry_fid -> list of furrified vanilla fids (to add)
        furry_to_furrified: dict[int, list[int]] = {}

        for assignment in self.ctx.assignments.values():
            vanilla_rec = self.races.get(assignment.vanilla_id)
            furry_rec = self.races.get(assignment.furry_id)
            if vanilla_rec is None or furry_rec is None:
                continue
            v_fid = vanilla_rec.normalize_form_id(vanilla_rec.form_id).value
            f_fid = furry_rec.normalize_form_id(furry_rec.form_id).value
            vanilla_to_furry[v_fid] = f_fid
            furry_to_furrified.setdefault(f_fid, []).append(v_fid)

        # Also include subraces
        for subrace in self.ctx.subraces.values():
            furry_rec = self.races.get(subrace.furry_id)
            subrace_rec = self.races.get(subrace.name)
            if furry_rec is None or subrace_rec is None:
                continue
            f_fid = furry_rec.normalize_form_id(furry_rec.form_id).value
            s_fid = subrace_rec.form_id.value  # local, already sentinel
            furry_to_furrified.setdefault(f_fid, []).append(s_fid)

        # Build FLST lookup (normalized FormID -> winning record)
        flst_by_fid: dict[int, Record] = {}
        for plugin in plugins:
            for rec in plugin.get_records_by_signature('FLST'):
                norm = rec.normalize_form_id(rec.form_id).value
                flst_by_fid[norm] = rec

        # Build normalized RACE FormID lookup for writing LNAMs
        # Maps normalized FormID value -> FormID object
        race_fid_lookup: dict[int, FormID] = {}
        for plugin in plugins:
            for rec in plugin.get_records_by_signature('RACE'):
                nfid = rec.normalize_form_id(rec.form_id)
                race_fid_lookup[nfid.value] = nfid
        # Include local races (subraces with sentinel FormIDs)
        for edid, rec in self.races.items():
            race_fid_lookup[rec.form_id.value] = rec.form_id

        # Track which FLSTs we've already processed (by normalized FormID)
        processed_flsts: set[int] = set()
        count = 0

        # Walk all HDPT records (winning overrides only)
        winning_hdpts: dict[int, Record] = {}
        for plugin in plugins:
            for rec in plugin.get_records_by_signature('HDPT'):
                winning_hdpts[rec.form_id.object_index] = rec

        for hdpt in winning_hdpts.values():
            rnam = hdpt.get_subrecord('RNAM')
            if rnam is None:
                continue
            flst_norm = hdpt.plugin.normalize_form_id(
                rnam.get_form_id()).value
            if flst_norm in processed_flsts:
                continue

            flst_rec = flst_by_fid.get(flst_norm)
            if flst_rec is None:
                continue

            # Read current race list as normalized FormIDs
            current_fids = []
            for sr in flst_rec.get_subrecords('LNAM'):
                raw = sr.get_form_id()
                current_fids.append(
                    flst_rec.normalize_form_id(raw).value)

            # Build new race list
            new_fids = []
            changed = False
            for fid in current_fids:
                if fid in vanilla_to_furry:
                    changed = True
                    continue

                new_fids.append(fid)

                if fid in furry_to_furrified:
                    for furrified_fid in furry_to_furrified[fid]:
                        if furrified_fid not in new_fids:
                            new_fids.append(furrified_fid)
                            changed = True

            if changed:
                # Get existing override or create one
                patched_fid = self.patch.denormalize_form_id(
                    flst_rec.normalize_form_id(flst_rec.form_id))
                patched_flst = self.patch.get_record_by_form_id(patched_fid)
                if patched_flst is None:
                    patched_flst = self._copy_record(flst_rec)
                patched_flst.remove_subrecords('LNAM')
                for fid in new_fids:
                    race_formid = race_fid_lookup.get(fid)
                    if race_formid is None:
                        log.warning(
                            "Race FormID %#010x not found for FLST LNAM",
                            fid)
                        continue
                    sr = patched_flst.add_subrecord('LNAM', b'\x00\x00\x00\x00')
                    self.patch.write_form_id(sr, 0, race_formid)
                count += 1

            processed_flsts.add(flst_norm)

        log.debug(f"Modified {count} headpart FormLists")
        return count

    # -- Armor furrification --

    def _build_armor_fallbacks(self) -> dict[int, list[int]]:
        """Build fallback race map from furry race RNAM subrecords.

        Each furry race has an RNAM pointing to its "armor race" -- the
        race whose armor meshes fit it. If an ARMA references the armor
        race but not the furry race, we should still add the furrified
        vanilla race.

        Returns: fallback_obj -> list of normalized vanilla FormIDs.
        """
        fallbacks: dict[int, list[FormID]] = {}
        for assignment in self.ctx.assignments.values():
            furry_rec = self.races.get(assignment.furry_id)
            vanilla_rec = self.races.get(assignment.vanilla_id)
            if furry_rec is None or vanilla_rec is None:
                continue
            rnam = furry_rec.get_subrecord('RNAM')
            if rnam is None or rnam.size < 4:
                continue
            fb_fid = rnam.get_form_id()
            if fb_fid.value == 0:
                continue
            fb_obj = fb_fid.object_index
            furry_obj = furry_rec.form_id.object_index
            if fb_obj == furry_obj:
                continue
            v_norm = vanilla_rec.normalize_form_id(vanilla_rec.form_id)
            fallbacks.setdefault(fb_obj, []).append(v_norm)
        return fallbacks


    def furrify_all_armor(self, plugins) -> int:
        """Adjust armor addon race lists driven by ARMO addon order.

        After race furrification, vanilla races like NordRace have furry
        head meshes. This method walks each ARMO's ARMA list (MODL refs)
        in order. For each furrified vanilla race, the first ARMA in the
        list that has the corresponding furry race wins -- that ARMA gets
        the furrified vanilla race added. ARMAs that lose the priority
        contest (or have no furry/fallback race) get furrified vanilla
        races removed.

        Must be called after merge_armor_overrides() so the ARMO addon
        lists are complete.

        Returns count of ARMA records modified.
        """
        from .armor import get_bodypart_flags

        # furry_obj -> list of (vanilla_obj, vanilla_norm_fid)
        furry_obj_to_vanilla: dict[int, list[tuple[int, FormID]]] = {}
        for a in self.ctx.assignments.values():
            furry_rec = self.races.get(a.furry_id)
            vanilla_rec = self.races.get(a.vanilla_id)
            if furry_rec and vanilla_rec:
                f_obj = furry_rec.form_id.object_index
                v_obj = vanilla_rec.form_id.object_index
                v_fid = vanilla_rec.normalize_form_id(vanilla_rec.form_id)
                furry_obj_to_vanilla.setdefault(f_obj, []).append(
                    (v_obj, v_fid))
        furry_objs: set[int] = set(furry_obj_to_vanilla.keys())

        # All furrified vanilla race obj_ids
        furrified_objs: set[int] = set()
        for a in self.ctx.assignments.values():
            vanilla_rec = self.races.get(a.vanilla_id)
            if vanilla_rec:
                furrified_objs.add(vanilla_rec.form_id.object_index)

        # Fallback: fallback_obj -> list of (vanilla_obj, vanilla_norm_fid)
        armor_fallbacks_raw = self._build_armor_fallbacks()
        armor_fallbacks: dict[int, list[tuple[int, FormID]]] = {}
        for fb_obj, v_fids in armor_fallbacks_raw.items():
            armor_fallbacks[fb_obj] = [
                (fid.object_index, fid) for fid in v_fids]
        fallback_objs: set[int] = set(armor_fallbacks.keys())

        # Winning ARMA records (last per obj_id, including patch)
        winning_armas: dict[int, Record] = {}
        for plugin in plugins:
            for arma in plugin.get_records_by_signature('ARMA'):
                obj_id = arma.form_id.value & 0x00FFFFFF
                winning_armas[obj_id] = arma
        for arma in self.patch.get_records_by_signature('ARMA'):
            obj_id = arma.form_id.value & 0x00FFFFFF
            winning_armas[obj_id] = arma

        # Helper: get race obj_ids from an ARMA
        def arma_race_objs(arma_rec):
            objs = set()
            rnam = arma_rec.get_subrecord('RNAM')
            if rnam and rnam.size >= 4:
                objs.add(rnam.get_form_id().object_index)
            for sr in arma_rec.get_subrecords('MODL'):
                if sr.size >= 4:
                    objs.add(sr.get_form_id().object_index)
            return objs

        # Walk all ARMO records; for each, resolve the ARMA priority
        # arma_obj -> set of normalized vanilla FormIDs to add
        arma_adds: dict[int, set] = {}
        # arma_obj -> set of vanilla_objs to remove
        arma_removes: dict[int, set[int]] = {}

        winning_armos: dict[int, Record] = {}
        for plugin in plugins:
            for armo in plugin.get_records_by_signature('ARMO'):
                obj_id = armo.form_id.value & 0x00FFFFFF
                winning_armos[obj_id] = armo
        # Patch overrides from merge_armor_overrides win over everything
        for armo in self.patch.get_records_by_signature('ARMO'):
            obj_id = armo.form_id.value & 0x00FFFFFF
            winning_armos[obj_id] = armo

        for armo in winning_armos.values():
            # Get this ARMO's ARMA refs in order
            arma_refs = []
            for sr in armo.get_subrecords('MODL'):
                if sr.size >= 4:
                    arma_obj = sr.get_uint32() & 0x00FFFFFF
                    arma_rec = winning_armas.get(arma_obj)
                    if arma_rec:
                        bp = get_bodypart_flags(arma_rec)
                        if bp & FURRIFIABLE_BODYPARTS:
                            arma_refs.append((arma_obj, arma_rec))

            if not arma_refs:
                continue

            # For each furrified vanilla race, find the first ARMA in
            # the list that has its furry race (or fallback).
            # Track which ARMA "owns" each vanilla race (claimed globally,
            # and per-ARMA so we know which to keep vs remove).
            claimed: set[int] = set()  # vanilla objs assigned to an ARMA
            # arma_obj -> set of vanilla objs this ARMA owns
            arma_owns: dict[int, set[int]] = {}

            for arma_obj, arma_rec in arma_refs:
                race_objs = arma_race_objs(arma_rec)

                # Which vanilla races can this ARMA claim?
                claimable: list[tuple[int, int]] = []

                # Direct furry race matches
                for f_obj in (race_objs & furry_objs):
                    for v_obj, v_fid in furry_obj_to_vanilla[f_obj]:
                        if v_obj not in claimed:
                            claimable.append((v_obj, v_fid))

                # Fallback matches (only if no direct furry)
                if not (race_objs & furry_objs):
                    for fb_obj in (race_objs & fallback_objs):
                        for v_obj, v_fid in armor_fallbacks[fb_obj]:
                            if v_obj not in claimed:
                                claimable.append((v_obj, v_fid))

                if claimable:
                    owns = arma_owns.setdefault(arma_obj, set())
                    adds = arma_adds.setdefault(arma_obj, set())
                    for v_obj, v_fid in claimable:
                        claimed.add(v_obj)
                        owns.add(v_obj)
                        # Only add if not already on this ARMA
                        if v_obj not in race_objs:
                            adds.add(v_fid)

            # Remove furrified vanilla races from ARMAs that don't
            # own them. Races already present but owned stay; races
            # present but not owned get removed.
            for arma_obj, arma_rec in arma_refs:
                race_objs = arma_race_objs(arma_rec)
                owned = arma_owns.get(arma_obj, set())
                removable = (race_objs & furrified_objs) - owned
                if removable:
                    removes = arma_removes.setdefault(arma_obj, set())
                    removes |= removable

        # Apply changes to ARMA records
        count = 0
        all_affected = set(arma_adds.keys()) | set(arma_removes.keys())
        for arma_obj in all_affected:
            arma_rec = winning_armas.get(arma_obj)
            if arma_rec is None:
                continue

            adds = arma_adds.get(arma_obj, set())
            removes = arma_removes.get(arma_obj, set())
            # Don't remove races that are being added
            removes -= {fid.object_index for fid in adds}

            if not adds and not removes:
                continue

            if arma_rec.plugin is self.patch:
                patched = arma_rec
            else:
                patched = self._copy_record(arma_rec)

            if removes:
                to_remove = []
                for sr in list(patched.get_subrecords('MODL')):
                    if sr.size >= 4:
                        obj = sr.get_form_id().object_index
                        if obj in removes:
                            to_remove.append(sr)
                for sr in to_remove:
                    patched.remove_subrecord(sr)

            for v_fid in adds:
                sr = patched.add_subrecord('MODL', b'\x00\x00\x00\x00')
                self.patch.write_form_id(sr, 0, v_fid)

            count += 1

        log.debug(f"Modified {count} armor addon records")
        return count


    # Priority order for choosing the base keyword/addon set.
    # The first match in this list is used as the base; mod overrides
    # then add on top of it. This ensures that USSEP fixes (keyword
    # removals, addon corrections) are preserved while mod additions
    # are still collected.
    _ARMOR_BASE_PRIORITY = [
        'unofficial skyrim special edition patch.esp',
        'dawnguard.esm',
        'hearthfires.esm',
        'dragonborn.esm',
        'update.esm',
        'skyrim.esm',
    ]


    def _get_record_objs(self, record: Record, sig: str) -> set[int]:
        """Get obj_ids from a record's subrecords of a given signature."""
        return {sr.get_uint32() & 0x00FFFFFF
                for sr in record.get_subrecords(sig)
                if sr.size >= 4}


    def _find_base_override(self, overrides: list[Record]) -> Record:
        """Find the most authoritative override for base keywords/addons.

        Returns the override from the highest-priority plugin, or the
        first override if none match the priority list.
        """
        by_name: dict[str, Record] = {}
        for rec in overrides:
            if rec.plugin and rec.plugin.file_path:
                name = rec.plugin.file_path.name.lower()
                by_name[name] = rec

        for priority_name in self._ARMOR_BASE_PRIORITY:
            if priority_name in by_name:
                return by_name[priority_name]

        return overrides[0]


    def _is_base_plugin(self, record: Record) -> bool:
        """Check if a record comes from a base/priority plugin."""
        if record.plugin and record.plugin.file_path:
            name = record.plugin.file_path.name.lower()
            return name in self._ARMOR_BASE_PRIORITY
        return False


    def merge_armor_overrides(self, plugins) -> int:
        """Merge ARMA references and keywords across ARMO overrides.

        When multiple mods override the same ARMO to add their ARMA
        (armor addon) or keywords, only the winning override survives.
        This method merges MODL (ARMA refs) and KWDA (keywords) using:

        1. Find the best base override (USSEP > DLCs > Update > Skyrim)
        2. Start with the base's MODL/KWDA as the authoritative set
        3. Add any MODL/KWDA introduced by non-base mod overrides
        4. Sort MODL by plugin order (mod ARMAs first, base last) so
           furrify_all_armor's priority-by-order works correctly

        Returns count of ARMO records merged.
        """
        # Build plugin name -> load index for sorting
        plugin_index: dict[str, int] = {}
        for i, plugin in enumerate(plugins):
            if plugin.file_path:
                plugin_index[plugin.file_path.name.lower()] = i
        base_names = set(self._ARMOR_BASE_PRIORITY)

        def _plugin_sort_key(src_rec: Record) -> int:
            """Sort key: mod plugins by load order, base plugins last."""
            if src_rec.plugin and src_rec.plugin.file_path:
                name = src_rec.plugin.file_path.name.lower()
                idx = plugin_index.get(name, 0)
                if name in base_names:
                    # Base plugins sort after all mods
                    return 10000 + idx
                return idx
            return 0

        # Collect all overrides of each ARMO by obj_id
        armo_overrides: dict[int, list[Record]] = {}
        for plugin in plugins:
            for rec in plugin.get_records_by_signature('ARMO'):
                obj_id = rec.form_id.value & 0x00FFFFFF
                armo_overrides.setdefault(obj_id, []).append(rec)

        count = 0
        for obj_id, overrides in armo_overrides.items():
            if len(overrides) < 2:
                continue

            winner = overrides[-1]
            base = self._find_base_override(overrides)

            # Start with the base's sets as authoritative
            # Store normalized FormIDs keyed by obj_id
            merged_modl: dict[int, tuple[FormID, Record]] = {}
            merged_kwda: dict[int, FormID] = {}

            for sr in base.get_subrecords('MODL'):
                if sr.size >= 4:
                    nfid = base.normalize_form_id(sr.get_form_id())
                    merged_modl[nfid.object_index] = (nfid, base)
            for sr in base.get_subrecords('KWDA'):
                # KWDA is a packed array — iterate 4 bytes at a time
                for off in range(0, sr.size, 4):
                    fid = base.normalize_form_id(
                        FormID(struct.unpack_from('<I', sr.data, off)[0]))
                    merged_kwda[fid.object_index] = fid

            # Add entries from non-base overrides (mods)
            for rec in overrides:
                if rec is base:
                    continue
                if self._is_base_plugin(rec):
                    continue
                for sr in rec.get_subrecords('MODL'):
                    if sr.size >= 4:
                        nfid = rec.normalize_form_id(sr.get_form_id())
                        if nfid.object_index not in merged_modl:
                            merged_modl[nfid.object_index] = (nfid, rec)
                for sr in rec.get_subrecords('KWDA'):
                    for off in range(0, sr.size, 4):
                        fid = rec.normalize_form_id(
                            FormID(struct.unpack_from('<I', sr.data, off)[0]))
                        if fid.object_index not in merged_kwda:
                            merged_kwda[fid.object_index] = fid

            # Check if the winner already has the merged set
            winner_modl_list = [
                sr.get_form_id().object_index
                for sr in winner.get_subrecords('MODL')
                if sr.size >= 4]
            winner_kwda = self._get_record_objs(winner, 'KWDA')

            # Sort MODL: mod-added ARMAs first (by load order), base last
            sorted_modl = sorted(
                merged_modl.items(),
                key=lambda item: _plugin_sort_key(item[1][1]))
            sorted_modl_objs = [obj for obj, _ in sorted_modl]

            need_modl = (sorted_modl_objs != winner_modl_list)
            need_kwda = set(merged_kwda.keys()) != winner_kwda

            if not need_modl and not need_kwda:
                continue

            if winner.plugin is self.patch:
                patched = winner
            else:
                patched = self._copy_record(winner)

            # Replace MODL list with sorted merged set
            if need_modl:
                patched.remove_subrecords('MODL')
                for m_obj, (nfid, src_rec) in sorted_modl:
                    sr = patched.add_subrecord('MODL', b'\x00\x00\x00\x00')
                    self.patch.write_form_id(sr, 0, nfid)

            # Replace KWDA with a single subrecord containing all keywords
            if need_kwda:
                patched.remove_subrecords('KWDA')
                kwda_data = bytearray(4 * len(merged_kwda))
                sr = patched.add_subrecord('KWDA', bytes(kwda_data))
                for i, (k_obj, nfid) in enumerate(merged_kwda.items()):
                    self.patch.write_form_id(sr, i * 4, nfid)
                ksiz = patched.get_subrecord('KSIZ')
                if ksiz:
                    ksiz.data = struct.pack('<I', len(merged_kwda))
                    ksiz.modified = True
                else:
                    patched.add_subrecord(
                        'KSIZ', struct.pack('<I', len(merged_kwda)))

            count += 1

        log.debug(f"Merged overrides in {count} ARMO records")
        return count

    # -- Statistics --

    def print_statistics(self) -> None:
        """Print post-run summary statistics."""
        total = sum(self.stats_race_counts.values())
        if total == 0:
            return

        log.info("")
        log.info("========== RACE DISTRIBUTION ==========")
        for race_id in sorted(self.stats_race_counts,
                              key=lambda r: -self.stats_race_counts[r]):
            n = self.stats_race_counts[race_id]
            pct = 100 * n / total
            log.info(f"  {race_id}: {n} ({pct:.1f}%)")
        log.info(f"  Total: {total}")

        for label, hair_dict in [("MALE", self.stats_hair_male),
                                 ("FEMALE", self.stats_hair_female)]:
            if not hair_dict:
                continue
            hair_total = sum(hair_dict.values())
            log.info("")
            log.info(f"========== {label} HAIR DISTRIBUTION ==========")
            for hp_id in sorted(hair_dict,
                                key=lambda h: -hair_dict[h]):
                n = hair_dict[hp_id]
                pct = 100 * n / hair_total
                log.info(f"  {hp_id}: {n} ({pct:.1f}%)")
            log.info(f"  Total: {hair_total}")
