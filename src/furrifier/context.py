"""FurryContext -- bundles all state needed for furrification.

Instead of passing patch, ctx, races, headparts, tints, etc. as separate
parameters to every function, FurryContext holds them all and exposes
furrification methods directly.
"""

from __future__ import annotations

import logging
import struct
from typing import Optional

from esplib import Plugin, Record

from .models import Sex, HeadpartType, HeadpartInfo, Bodypart
from .race_defs import RaceDefContext
from .vanilla_setup import unalias
from .setup import is_npc_female, is_child_race, get_headpart_type
from .headparts import load_npc_labels, find_similar_headpart
from .tints import RaceTintData, TintChoice, choose_furry_tints

log = logging.getLogger(__name__)

# Bodypart flags that indicate armor needing furry race support
FURRIFIABLE_BODYPARTS = (
    Bodypart.HEAD | Bodypart.HAIR | Bodypart.HANDS |
    Bodypart.LONGHAIR | Bodypart.CIRCLET
)


class FurryContext:
    """All state needed to furrify NPCs, armor, and schlongs."""

    def __init__(self,
                 patch: Plugin,
                 ctx: RaceDefContext,
                 races: dict[str, Record],
                 all_headparts: dict[str, HeadpartInfo],
                 race_headparts: dict,
                 race_tints: dict[str, RaceTintData],
                 max_tint_layers: int = 200):
        self.patch = patch
        self.ctx = ctx
        self.races = races
        self.all_headparts = all_headparts
        self.race_headparts = race_headparts
        self.race_tints = race_tints
        self.max_tint_layers = max_tint_layers

    # -- NPC furrification --

    def determine_npc_sex(self, npc: Record, race: Optional[Record]) -> Sex:
        """Determine the NPC's Sex enum from ACBS flags and race."""
        female = is_npc_female(npc)
        child = is_child_race(race) if race is not None else False
        return Sex.from_flags(female=female, child=child)

    def determine_npc_race(self, npc: Record,
                           ) -> Optional[tuple[str, str, str]]:
        """Determine vanilla, assigned, and furry race for an NPC.

        Returns (original_race_id, assigned_race_id, furry_race_id) or None
        if the NPC's race isn't furrifiable.
        """
        rnam = npc.get_subrecord('RNAM')
        if rnam is None:
            return None
        race_fid = rnam.get_uint32()

        original_race_id = None
        for edid, rec in self.races.items():
            if rec.form_id.value == race_fid:
                original_race_id = edid
                break

        if original_race_id is None:
            return None

        assigned_race_id = original_race_id

        npc_edid = unalias(npc.editor_id or '')
        if npc_edid in self.ctx.npc_races:
            assigned_race_id = self.ctx.npc_races[npc_edid]

        # TODO: resolve faction FormIDs and check ctx.faction_races

        if assigned_race_id in self.ctx.assignments:
            furry_race_id = self.ctx.assignments[assigned_race_id].furry_id
        elif assigned_race_id in self.ctx.subraces:
            furry_race_id = self.ctx.subraces[assigned_race_id].furry_id
        else:
            return None

        return (original_race_id, assigned_race_id, furry_race_id)

    def furrify_npc(self, npc: Record) -> Optional[Record]:
        """Furrify a single NPC.

        Creates an override in the patch plugin with furry race, headparts,
        and tint layers. Returns the patched record, or None if skipped.
        """
        # Skip chargen presets
        acbs = npc.get_subrecord('ACBS')
        if acbs and acbs.size >= 4:
            flags = acbs.get_uint32(0)
            if flags & 4:  # Is CharGen Face Preset
                return None

        race_result = self.determine_npc_race(npc)
        if race_result is None:
            return None

        original_race_id, assigned_race_id, furry_race_id = race_result
        race_record = self.races.get(original_race_id)
        npc_sex = self.determine_npc_sex(npc, race_record)
        npc_alias = unalias(npc.editor_id or str(npc.form_id))

        log.info(f"Furrifying {npc_alias}: {original_race_id} -> {furry_race_id}")

        patched = self.patch.copy_record(npc)

        # Only change RNAM for subraces (e.g. Breton -> Reachman).
        # Normal races (e.g. Nord) are furrified at the race record level,
        # so the NPC keeps its original RNAM.
        if assigned_race_id != original_race_id:
            if assigned_race_id in self.races:
                race_rec = self.races[assigned_race_id]
                patched.get_subrecord('RNAM').set_uint32(
                    0, race_rec.form_id.value)

        # Remove vanilla character customization
        patched.remove_subrecords('FTST')
        patched.remove_subrecords('QNAM')
        patched.remove_subrecords('NAM9')
        patched.remove_subrecords('TINI')
        patched.remove_subrecords('TINC')
        patched.remove_subrecords('TIAS')
        patched.remove_subrecords('TINV')

        # Load NPC labels for headpart matching
        labels = load_npc_labels(npc, self.ctx)

        # Replace headparts
        old_headpart_srs = npc.get_subrecords('PNAM')
        patched.remove_subrecords('PNAM')

        for old_sr in old_headpart_srs:
            old_fid = old_sr.get_uint32()
            old_hp = None
            for hp in self.all_headparts.values():
                if hp.record and hp.record.form_id.value == old_fid:
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
                patched.add_subrecord(
                    'PNAM', struct.pack('<I', new_hp.record.form_id.value))

        # Apply furry tint layers
        npc_tint_classes: set[str] = set()
        # TODO: extract vanilla NPC tint classes for decoration layer matching

        tint_choices = choose_furry_tints(
            npc_alias, npc_sex, furry_race_id,
            npc_tint_classes, self.race_tints, self.max_tint_layers,
        )

        for choice in tint_choices:
            patched.add_subrecord('TINI', struct.pack('<H', choice.tini))
            patched.add_subrecord('TINC', struct.pack('<I', choice.tinc))
            patched.add_subrecord('TIAS', struct.pack('<H', choice.tias))
            patched.add_subrecord('TINV', struct.pack('<f', choice.tinv))

        return patched

    def furrify_all_npcs(self, plugins: list[Plugin],
                         furrify_male: bool = True,
                         furrify_female: bool = True) -> int:
        """Furrify all NPCs across the load order. Returns count."""
        count = 0
        for plugin in plugins:
            if plugin is None:
                continue
            npcs = plugin.get_records_by_signature('NPC_')
            for i, npc in enumerate(npcs):
                if (i % 500) == 0 and i > 0:
                    log.info(f"  {plugin.file_path.name}: {i}/{len(npcs)}")

                if not furrify_male and not is_npc_female(npc):
                    continue
                if not furrify_female and is_npc_female(npc):
                    continue

                result = self.furrify_npc(npc)
                if result is not None:
                    count += 1

            log.info(f"Processed {plugin.file_path.name}: {len(npcs)} NPCs")

        log.info(f"Total NPCs furrified: {count}")
        return count

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
                     furry_race: Record) -> Record:
        """Furrify a vanilla race by copying key subrecords from the furry race.

        Copies WNAM (skin), RNAM (armor race), and the entire Head Data
        section (head parts, tint masks, presets) from the furry race.
        FormIDs are remapped to the patch's master list.

        Returns the patched race record.
        """
        patched = self.patch.copy_record(vanilla_race)
        furry_plugin = getattr(furry_race, '_plugin', None)

        # Copy simple FormID subrecords (WNAM, RNAM)
        for sig in self._RACE_COPY_SIGS:
            src_sr = furry_race.get_subrecord(sig)
            if src_sr is not None and src_sr.size == 4:
                raw_fid = src_sr.get_uint32()
                if furry_plugin:
                    remapped = self.patch.remap_formid(raw_fid, furry_plugin)
                else:
                    remapped = raw_fid
                dst_sr = patched.get_subrecord(sig)
                if dst_sr is not None:
                    dst_sr.set_uint32(0, remapped)
                    dst_sr.modified = True
                else:
                    patched.add_subrecord(
                        sig, struct.pack('<I', remapped))

        # Replace Head Data: remove vanilla head data, insert furry head data
        self._replace_head_data(patched, furry_race, furry_plugin)

        log.info(f"Furrified race {vanilla_race.editor_id} "
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
            new_data = bytearray(sr.data)
            if (sr.signature in self._HEAD_FORMID_SIGS
                    and sr.size == 4 and furry_plugin):
                raw_fid = struct.unpack('<I', sr.data)[0]
                remapped = self.patch.remap_formid(raw_fid, furry_plugin)
                new_data = bytearray(struct.pack('<I', remapped))
            new_sr = SubRecord(sr.signature, bytes(new_data))
            patched.subrecords.insert(head_start, new_sr)
            head_start += 1

        patched.modified = True


    def furrify_all_races(self) -> int:
        """Furrify all vanilla races that have furry assignments.

        Returns count of races furrified.
        """
        count = 0
        for assignment in self.ctx.assignments.values():
            vanilla_rec = self.races.get(assignment.vanilla_id)
            furry_rec = self.races.get(assignment.furry_id)
            if vanilla_rec is None or furry_rec is None:
                continue
            self.furrify_race(vanilla_rec, furry_rec)
            count += 1

        log.info(f"Furrified {count} races")
        return count

    # -- Armor furrification --

    def furrify_all_armor(self, plugins: list[Plugin]) -> int:
        """Add furry races to all armor addons that support vanilla equivalents.

        Returns count of ARMA records modified.
        """
        race_fid_map = {}
        for a in self.ctx.assignments.values():
            vanilla_rec = self.races.get(a.vanilla_id)
            furry_rec = self.races.get(a.furry_id)
            if vanilla_rec and furry_rec:
                race_fid_map[vanilla_rec.form_id.value] = \
                    furry_rec.form_id.value

        count = 0
        for plugin in plugins:
            if plugin is None:
                continue
            for arma in plugin.get_records_by_signature('ARMA'):
                from .armor import get_bodypart_flags, arma_has_race
                bp_flags = get_bodypart_flags(arma)
                if not (bp_flags & FURRIFIABLE_BODYPARTS):
                    continue

                for vanilla_fid, furry_fid in race_fid_map.items():
                    if arma_has_race(arma, vanilla_fid) and \
                       not arma_has_race(arma, furry_fid):
                        patched = self.patch.copy_record(arma)
                        patched.add_subrecord(
                            'MODL', struct.pack('<I', furry_fid))
                        count += 1
                        break

        log.info(f"Modified {count} armor addon records")
        return count
