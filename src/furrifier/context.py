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
from .furry_load import is_npc_female, is_child_race
from .headparts import load_npc_labels, find_similar_headpart
from .tints import choose_furry_tints

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
                 race_tints: dict,
                 all_plugins: list[Plugin] = None,
                 max_tint_layers: int = 200):
        self.patch = patch
        self.ctx = ctx
        self.races = races
        self.all_headparts = all_headparts
        self.race_headparts = race_headparts
        self.race_tints = race_tints
        self._all_plugins = all_plugins or []
        self.max_tint_layers = max_tint_layers

    # -- NPC furrification --

    def determine_npc_sex(self, npc: Record, race: Optional[Record]) -> Sex:
        """Determine the NPC's Sex enum from ACBS flags and race."""
        female = is_npc_female(npc)
        child = is_child_race(race) if race is not None else False
        return Sex.from_flags(female=female, child=child)

    def _add_headpart_pnam(self, record: Record, hp: HeadpartInfo) -> None:
        """Add a PNAM subrecord for a headpart, remapping the FormID."""
        hp_fid = hp.record.form_id.value
        hp_plugin = getattr(hp.record, '_plugin', None)
        if hp_plugin:
            hp_fid = self.patch.remap_formid(hp_fid, hp_plugin)
        record.add_subrecord('PNAM', struct.pack('<I', hp_fid))


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
        acbs = npc['ACBS']
        if acbs and acbs['flags'].Is_CharGen_Face_Preset:
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

            # Skip beards — furry races don't use them
            if old_hp.hp_type == HeadpartType.FACIAL_HAIR:
                continue

            # Eyebrows slot is used for earrings on furry races.
            # Give NPCs a 30% chance of earrings. TODO: more
            # controllable mechanism for earring assignment.
            if old_hp.hp_type == HeadpartType.EYEBROWS:
                from .util import hash_string
                if hash_string(npc_alias, 9999, 100) >= 30:
                    continue

            new_hp = find_similar_headpart(
                old_hp, npc_alias, npc_sex, labels,
                furry_race_id, self.race_headparts, self.all_headparts,
                self.ctx,
            )
            if new_hp and new_hp.record:
                self._add_headpart_pnam(patched, new_hp)

        # TODO: beard matching — for now beards are skipped entirely

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
                skin_tone_intensity = choice.tinv

        # Calculate QNAM from skin tone tint
        if skin_tone_color:
            self._apply_qnam_from_color(patched, skin_tone_color,
                                        skin_tone_intensity)

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
        for plugin in self._all_plugins:
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

        QNAM = intensity * component / 255 for each RGB channel.
        """
        qr = round(intensity * color[0])
        qg = round(intensity * color[1])
        qb = round(intensity * color[2])

        record.add_subrecord('QNAM', struct.pack('<fff',
                             qr / 255.0, qg / 255.0, qb / 255.0))

    def furrify_all_npcs(self, plugins,
                         furrify_male: bool = True,
                         furrify_female: bool = True) -> int:
        """Furrify all NPCs across the load order. Returns count."""
        count = 0
        for plugin in plugins:
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

    def furrify_all_armor(self, plugins) -> int:
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
