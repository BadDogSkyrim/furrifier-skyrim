"""FurryContext -- bundles all state needed for furrification.

Instead of passing patch, ctx, races, headparts, tints, etc. as separate
parameters to every function, FurryContext holds them all and exposes
furrification methods directly.
"""

from __future__ import annotations

import logging
import struct
import threading
from typing import Optional

from esplib import Plugin, PluginSet, Record
from esplib.utils import FormID

from .models import Breed, Sex, HeadpartType, HeadpartInfo, Bodypart
from .race_defs import RaceDefContext
from .vanilla_setup import unalias
from .furry_load import is_npc_female, is_child_race
from .headparts import (
    load_npc_labels, find_similar_headpart, _should_assign,
    _PROBABILITY_GATED_TYPES, _ci_lookup, _is_excluded,
)
from .util import (
    get_bodypart_flags, hash_string, short_race_name, record_key, ref_key,
)
from .tints import choose_breed_tints, choose_furry_tints

log = logging.getLogger(__name__)

# Bodypart flags that indicate armor needing furry race support, split
# on biped slot 52 (Bodypart.SCHLONG) -- SOS's schlong slot.
#
# --armor and --schlongs partition this set rather than overlapping on
# it: --armor alone means "every furrifiable slot EXCEPT 52", --schlongs
# alone means "slot 52 and nothing else", both together mean all of it.
# Before the split, --armor furrified sheath ARMAs too, so a run that
# wanted only the non-schlong armor could not ask for it -- the SFW
# build had to exclude the SOS plugins from --plugins to get that
# effect, and the NSFW patch carried 68 generic armor ARMAs it had no
# use for. See armor_bodypart_mask().
ARMOR_ONLY_BODYPARTS = (
    Bodypart.HEAD | Bodypart.HAIR | Bodypart.LONGHAIR | Bodypart.CIRCLET
)
SCHLONG_BODYPARTS = Bodypart.SCHLONG
FURRIFIABLE_BODYPARTS = ARMOR_ONLY_BODYPARTS | SCHLONG_BODYPARTS


def armor_bodypart_mask(furrify_armor: bool, furrify_schlongs: bool) -> int:
    """Bodypart flags the armor pass may touch, for one flag pair.

    Returns 0 when neither flag is set, which callers read as "skip the
    armor pass entirely" -- a zero mask matches no ARMA, so running it
    anyway would be a no-op, just a slower one.

    Passing both flags reproduces FURRIFIABLE_BODYPARTS exactly, so the
    default behaviour of every existing caller is unchanged.
    """
    mask = 0
    if furrify_armor:
        mask |= ARMOR_ONLY_BODYPARTS
    if furrify_schlongs:
        mask |= SCHLONG_BODYPARTS
    return int(mask)


def armo_in_bodypart_scope(modl_keys, armas_by_key, bodypart_mask) -> bool:
    """Is this ARMO's addon set in scope for a run using `bodypart_mask`?

    True unless EVERY furrifiable addon it carries sits outside the mask.

    An ARMO with no furrifiable addons at all stays in scope. Merging
    those is plain load-order conflict resolution -- unrelated to which
    bodypart slots this run was asked to furrify -- and dropping them
    would resurrect armor conflicts the furrifier has always fixed.

    With the full mask this is always True, so the unfiltered path is
    exactly what it was before the slot-52 split existed.
    """
    saw_furrifiable = False
    for key in modl_keys:
        arma = armas_by_key.get(key)
        if arma is None:
            continue
        bp = get_bodypart_flags(arma)
        if bp & bodypart_mask:
            return True
        if bp & FURRIFIABLE_BODYPARTS:
            saw_furrifiable = True
    return not saw_furrifiable

# Race EditorID variant suffixes, longest-match first.
_RACE_VARIANT_SUFFIXES = ('ChildVampire', 'Vampire', 'Child')

# High byte of a plugin-local FormID: a reference to one of the plugin's
# OWN records, stamped with the real self index at save time.
_LOCAL_SENTINEL_HIGH = 0xFF000000

# RACE DATA layout: skill boosts (14) + padding (2) + male/female height
# and weight (4 floats) puts the flags uint32 at offset 32. Bit 0 is
# Playable — see is_child_race() in furry_load.py for bit 2 (Child).
_RACE_DATA_FLAGS_OFFSET = 32
_RACE_FLAG_PLAYABLE = 0x00000001


def _set_race_playable(race: Record, playable: bool) -> bool:
    """Set (or clear) the Playable flag in a RACE record's DATA flags.

    Returns True if the flag was written, False if DATA is missing or
    too short to hold the flags field.
    """
    data = race.get_subrecord('DATA')
    if data is None or data.size < _RACE_DATA_FLAGS_OFFSET + 4:
        return False
    flags = data.get_uint32(_RACE_DATA_FLAGS_OFFSET)
    if playable:
        flags |= _RACE_FLAG_PLAYABLE
    else:
        flags &= ~_RACE_FLAG_PLAYABLE
    data.set_uint32(_RACE_DATA_FLAGS_OFFSET, flags)
    return True


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


# Stamped into the patch's TES4 CNAM (author) at save time. Future
# runs treat any plugin whose author starts with this prefix as
# furrifier output. RNAM-based detection couldn't catch normal-race
# assignments (RNAM stays at the vanilla race) or subrace assignments
# (RNAM points at a patch-created subrace EDID we didn't track) —
# plugin identity sidesteps both.
FURRIFIER_AUTHOR_PREFIX = "SkyrimFurrifier"


def furrifier_patch_names(plugin_set) -> set[str]:
    """Lowercased filenames of every loaded plugin whose TES4 author
    marks it as furrifier output."""
    names: set[str] = set()
    for plugin in plugin_set:
        author = plugin.header.author or ""
        if author.startswith(FURRIFIER_AUTHOR_PREFIX) and plugin.file_path:
            names.add(plugin.file_path.name.lower())
    return names


def is_furrified(plugin_set, npc: Record,
                 furrifier_names: set[str]) -> bool:
    """True if any record in the NPC's override chain came from a
    furrifier patch. Plugin-identity check, independent of RNAM."""
    abs_fid = npc.normalize_form_id(npc.form_id)
    chain = plugin_set.get_override_chain(abs_fid)
    if chain is None:
        return False
    for rec in chain:
        if rec.plugin is None or rec.plugin.file_path is None:
            continue
        if rec.plugin.file_path.name.lower() in furrifier_names:
            return True
    return False


def find_pre_furry_record(plugin_set, npc: Record,
                          furrifier_names: set[str]) -> Optional[Record]:
    """Walk override chain top-to-bottom; return the first record
    whose source plugin isn't a furrifier patch.

    Returns None if every record in the chain came from a furrifier
    patch (NPC originated in a furry plugin and has no non-furry
    source we could re-derive from).
    """
    abs_fid = npc.normalize_form_id(npc.form_id)
    chain = plugin_set.get_override_chain(abs_fid)
    if chain is None:
        return None
    for rec in reversed(list(chain)):
        if rec.plugin is None or rec.plugin.file_path is None:
            continue
        if rec.plugin.file_path.name.lower() not in furrifier_names:
            return rec
    return None


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
        self._furrifier_patch_names_cache: Optional[set[str]] = None
        self._headpart_by_key_cache: Optional[dict[int, HeadpartInfo]] = None
        # NPCs left alone under --preserve-existing: already furrified
        # by an earlier patch, so we keep that patch's per-NPC choices
        # rather than re-deriving. They get no record in our patch, but
        # they still need FaceGen built from their existing definition —
        # otherwise choosing "preserve" silently means "no face".
        self.preserved_npcs: list = []
        # Statistics (populated during furrification)
        self.stats_race_counts: dict[str, int] = {}   # furry_race_id -> count
        self.stats_hair_male: dict[str, int] = {}     # headpart_edid -> count
        self.stats_hair_female: dict[str, int] = {}   # headpart_edid -> count


    def _copy_record(self, record, source_plugin=None):
        """Copy a record into the patch, with string fallback resolution."""
        return self.patch.copy_record(record, source_plugin)


    def headpart_by_key(self) -> dict[int, HeadpartInfo]:
        """Cached record-key -> HeadpartInfo index.

        `all_headparts` is keyed by EditorID; resolving a PNAM reference
        needs the other direction. Built once because the callers sit in
        per-NPC loops -- the linear scan this replaces walked all ~2500
        headparts per PNAM per NPC.
        """
        if self._headpart_by_key_cache is None:
            self._headpart_by_key_cache = {
                record_key(hp.record): hp
                for hp in self.all_headparts.values() if hp.record
            }
        return self._headpart_by_key_cache


    def furrifier_patch_names(self) -> set[str]:
        """Cached set of lowercased filenames for plugins whose TES4
        author marks them as prior furrifier output."""
        if self._furrifier_patch_names_cache is None:
            self._furrifier_patch_names_cache = furrifier_patch_names(
                self.plugin_set)
        return self._furrifier_patch_names_cache

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
                           ) -> Optional[tuple[str, str, str, Optional[Breed]]]:
        """Determine vanilla, assigned, and furry race + breed for an NPC.

        Returns (original_race_id, assigned_race_id, furry_race_id,
        breed) or None if the NPC's race isn't furrifiable.

        `furry_race_id` is the engine-level race EDID (used for RNAM
        and headpart pool lookups). `breed`, if non-None, constrains
        which headparts/tints get picked at patch time without changing
        the engine race. See PLAN_FURRIFIER_BREEDS.md.

        Breed assignment rules:
        - When a `[npc_races]` / `[faction_races]` / `vanilla→furry`
          mapping resolves directly to a breed name, that breed is used
          (no probability roll).
        - Otherwise, when the resolved race has breeds with non-zero
          probability, hash-roll across them to pick a breed (or none).
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

        # Resolve assigned_race_id (which may be a vanilla race, subrace,
        # or breed name) into a furry-race name string. The string itself
        # may still be a race or a breed; resolve_race_or_breed splits.
        if assigned_race_id in self.ctx.assignments:
            furry_name = self.ctx.assignments[assigned_race_id].furry_id
        elif assigned_race_id in self.ctx.subraces:
            furry_name = self.ctx.subraces[assigned_race_id].furry_id
        elif assigned_race_id in self.ctx.breeds:
            # Direct breed assignment via [npc_races] / [faction_races].
            furry_name = assigned_race_id
        else:
            return None

        furry_race_id, breed = self.ctx.resolve_race_or_breed(furry_name)

        # If no breed yet and the engine race has breeds with
        # probability > 0, hash-roll for one.
        if breed is None:
            npc_alias = unalias(npc.editor_id or str(npc.form_id))
            breed = self.ctx.roll_breed(npc_alias, furry_race_id)

        return (original_race_id, assigned_race_id, furry_race_id, breed)

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
        # Skyrim.esm:0x000007 is the Player base NPC. Overriding it from
        # a patch crashes chargen's face load — leave it alone.
        if (npc.plugin is not None and
                npc.plugin.normalize_form_id(npc.form_id).value == 0x07):
            return None

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
            # Resolve the override name — may be a race EDID or a
            # breed name. Breeds inherit the parent race's RNAM
            # (engine-level identity) and contribute their headpart /
            # tint constraints via the standard breed plumbing.
            assigned_race_id = override_furry_race
            furry_race_id, breed = self.ctx.resolve_race_or_breed(
                override_furry_race)
            # If the override named a race (not a breed) and that race
            # has breeds defined, hash-roll for a breed — same as the
            # determine_npc_race path. Without this, leveled-list
            # duplicates whose rule names a race miss every breed's
            # headpart/tint constraints (no horns, decoration tints
            # carried over from the source NPC).
            if breed is None:
                roll_alias = unalias(npc.editor_id or str(npc.form_id))
                breed = self.ctx.roll_breed(roll_alias, furry_race_id)
        else:
            race_result = self.determine_npc_race(npc)
            if race_result is None:
                return None
            original_race_id, assigned_race_id, furry_race_id, breed = race_result
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
                if log.isEnabledFor(logging.DEBUG):
                    log.debug(
                        "[rnam] %s -> %s: subrace fid=%08X norm=%08X "
                        "written=%08X (patch masters=%d)",
                        patched.editor_id, assigned_race_id,
                        int(subrace_rec.form_id), int(norm_fid),
                        rnam_sr.get_uint32(),
                        len(self.patch.header.masters))

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

        # Apply the breed/race/wildcard weight_range remap (if any).
        # NAM7 is the NPC's weight, vanilla 0-100 float; we linearly
        # map it onto (low, high) configured in the scheme. Default
        # range (0, 100) is identity → no change. Used so e.g. all
        # Mino NPCs end up at weight ≥ 50 regardless of source.
        sex_name_for_weight = 'Female' if npc_sex.is_female else 'Male'
        weight_lookup_name = (breed.name if breed is not None
                              else furry_race_id)
        weight_low, weight_high = self.ctx.get_weight_range(
            weight_lookup_name, sex_name_for_weight)
        if (weight_low, weight_high) != (0.0, 100.0):
            nam7 = patched.get_subrecord('NAM7')
            if nam7 is not None and nam7.size >= 4:
                vanilla_weight = struct.unpack('<f', nam7.data[:4])[0]
                # Clamp source to 0-100 in case a mod shipped weights
                # outside the vanilla range.
                clamped = max(0.0, min(100.0, vanilla_weight))
                remapped = (weight_low
                            + (clamped / 100.0) * (weight_high - weight_low))
                nam7.data = struct.pack('<f', remapped)

        # Load NPC labels for headpart matching
        labels = load_npc_labels(npc, self.ctx)

        # Replace headparts
        old_headpart_srs = npc.get_subrecords('PNAM')
        patched.remove_subrecords('PNAM')

        assigned_types: set[HeadpartType] = set()
        for old_sr in old_headpart_srs:
            old_hp = self.headpart_by_key().get(
                ref_key(npc, old_sr.get_form_id()))
            if old_hp is None:
                continue

            new_hp = find_similar_headpart(
                old_hp, npc_alias, npc_sex, labels,
                furry_race_id, self.race_headparts, self.all_headparts,
                self.ctx, breed=breed,
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
        sex_name = 'Female' if npc_sex.is_female else 'Male'
        for hp_type in _PROBABILITY_GATED_TYPES:
            if hp_type in assigned_types:
                continue
            if not _should_assign(npc_alias, furry_race_id, npc_sex,
                                  hp_type, self.ctx, breed=breed):
                continue
            # Apply the breed's (or race's) headpart whitelist when set.
            # Whitelist is authoritative — author intent overrides the
            # random-pool EXCLUDE filter. We DO cross-check whitelisted
            # EDIDs against this race's HDPT pool and warn on mismatch
            # (e.g. a deer horn whitelisted on a mino) so silent garbage
            # in the patch becomes a loud warning.
            lookup_name = breed.name if breed is not None else furry_race_id
            whitelist = self.ctx.get_headpart_rule(
                lookup_name, sex_name, hp_type.name).headpart_whitelist
            if whitelist:
                race_pool = self.race_headparts.get(
                    (hp_type, sex_key, furry_race_id), set())
                candidates = set()
                for edid in whitelist:
                    hp = _ci_lookup(self.all_headparts, edid)
                    if hp is None:
                        continue
                    if race_pool and hp.editor_id not in race_pool:
                        log.warning(
                            f"breed whitelist for {furry_race_id} "
                            f"{sex_name} {hp_type.name} names "
                            f"{edid!r} which isn't in this race's "
                            f"HDPT pool — patch may emit broken NPCs. "
                            f"Honoring author intent anyway.")
                    candidates.add(hp.editor_id)
                if not candidates:
                    log.warning(
                        f"breed whitelist {whitelist!r} for "
                        f"{hp_type.name} contains no known headparts "
                        f"— skipping {npc_alias}")
                    continue
            else:
                # Filter EXCLUDE-tagged HDPTs from the random pool.
                # build_race_headparts no longer drops them (that's a
                # selection-time policy now, applied here).
                pool = self.race_headparts.get(
                    (hp_type, sex_key, furry_race_id), set())
                candidates = {edid for edid in pool
                              if not _is_excluded(self.all_headparts[edid])}
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

        # Apply furry tint layers. When the breed defines explicit tint
        # rules (or `tints = []` for "no tints"), use the breed-driven
        # path; otherwise fall back to the unconstrained pool.
        breed_tint_rules = None
        if breed is not None:
            breed_tint_rules = self.ctx.get_tint_rules(breed.name, sex_name)
        if breed_tint_rules is not None:
            from .tints import choose_breed_tints, pick_uncovered_decorations
            race_data = self.race_tints.get((furry_race_id, npc_sex))
            if race_data is None:
                tint_choices = []
            else:
                tint_choices = choose_breed_tints(
                    npc_alias, breed_tint_rules, race_data,
                    self._form_id_for_edid)
                # Hybrid: preserve vanilla decoration intent
                # (Skull / Hand / Paint / Dirt) for any decoration
                # class the breed didn't author rules for. Without
                # this, a breed-tagged NPC like a Mino-LongHorn
                # version of DA06LvlOrcMelee silently loses the
                # vanilla skull paint just because the LongHorn
                # rules only cover fur layers.
                tint_choices.extend(pick_uncovered_decorations(
                    npc_alias, breed_tint_rules, race_data,
                    npc_tint_classes,
                ))
        else:
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
            hp = self.headpart_by_key().get(ref_key(patched, sr.get_form_id()))
            if hp is not None and hp.hp_type == HeadpartType.HAIR:
                is_female = npc_sex in (Sex.FEMALE_ADULT, Sex.FEMALE_CHILD)
                hair_dict = self.stats_hair_female if is_female \
                    else self.stats_hair_male
                hair_dict[hp.editor_id] = hair_dict.get(hp.editor_id, 0) + 1

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


    def _build_clfm_edid_index(self) -> dict[str, Record]:
        """CLFM EditorID → record. Keys are lowercased so scheme-supplied
        EDIDs match regardless of case; load-order-winning record wins
        on EDID collision (later plugins override earlier)."""
        index: dict[str, Record] = {}
        if self.plugin_set is None:
            return index
        for plugin in self.plugin_set:
            for rec in plugin.get_records_by_signature('CLFM'):
                edid = rec.editor_id
                if edid:
                    index[edid.lower()] = rec  # last wins = load-order winner
        return index


    def _build_clfm_form_id_index(self) -> dict[int, Record]:
        """CLFM full load-order-normalized FormID → record.

        Distinct from the pre-Phase-4 lower-24-bit lookup, which
        conflated records sharing an object index across different
        masters (e.g. CellanFur06GrayBrown vs Red01 at 0x0012DD).
        """
        index: dict[int, Record] = {}
        if self.plugin_set is None:
            return index
        for plugin in self.plugin_set:
            for rec in plugin.get_records_by_signature('CLFM'):
                fid = rec.normalize_form_id(rec.form_id).value
                index[fid] = rec  # later overrides win for the same FID
        return index


    def _ensure_clfm_indexes(self) -> None:
        """Lazy-build both CLFM indexes. Idempotent."""
        if not hasattr(self, '_clfm_by_edid_cache'):
            self._clfm_by_edid_cache = self._build_clfm_edid_index()
        if not hasattr(self, '_clfm_by_form_id_cache'):
            self._clfm_by_form_id_cache = self._build_clfm_form_id_index()


    def _resolve_color(self, tinc_fid: int) -> tuple[int, int, int, int]:
        """Resolve a load-order-normalized TINC FormID to RGBA.

        Callers must pass FormIDs already normalized to load-order
        space (TintAsset.presets does this at extraction time). Returns
        (255, 255, 255, 0) when no CLFM matches.
        """
        self._ensure_clfm_indexes()
        rec = self._clfm_by_form_id_cache.get(tinc_fid)
        if rec is not None:
            cnam = rec.get_subrecord('CNAM')
            if cnam and cnam.size >= 4:
                return (cnam.data[0], cnam.data[1],
                        cnam.data[2], cnam.data[3])
            if cnam and cnam.size >= 3:
                return (cnam.data[0], cnam.data[1], cnam.data[2], 0)
        return (255, 255, 255, 0)


    def _resolve_color_by_edid(self, edid: str) -> Optional[tuple[int, int, int, int]]:
        """Resolve a CLFM EditorID to its RGBA. None if not found."""
        self._ensure_clfm_indexes()
        rec = self._clfm_by_edid_cache.get(edid.lower())
        if rec is None:
            return None
        cnam = rec.get_subrecord('CNAM')
        if cnam is None:
            return None
        if cnam.size >= 4:
            return (cnam.data[0], cnam.data[1], cnam.data[2], cnam.data[3])
        if cnam.size >= 3:
            return (cnam.data[0], cnam.data[1], cnam.data[2], 0)
        return None


    def _form_id_for_edid(self, edid: str) -> Optional[int]:
        """Load-order-normalized FormID for a CLFM EDID. None if not found."""
        self._ensure_clfm_indexes()
        rec = self._clfm_by_edid_cache.get(edid.lower())
        if rec is None:
            return None
        return rec.normalize_form_id(rec.form_id).value


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

    def furrify_all_npcs(self, plugins, only_npc: Optional[str] = None,
                         cancel_event: "Optional[threading.Event]" = None,
                         preserve_existing: bool = False,
                         ) -> int:
        """Furrify all NPCs across the load order. Returns count.

        Only processes the winning override of each NPC (last in load
        order). Skips NPCs that have already been overridden by plugins
        loaded after the ones we're processing.

        `only_npc` (when set) restricts patching to a single NPC matched
        by EditorID (case-insensitive) or hex form-id object index.

        `cancel_event` (when set) is checked once per NPC; raises
        `main.CancelledError` so the caller can drop out of the run.

        `preserve_existing` (default False): when True, NPCs whose
        winning override is already a furrifier output (RNAM points at
        a scheme target race) are skipped so a prior patch's
        per-NPC choices are preserved. When False (default), the
        override chain is walked back to the topmost non-furry record
        and re-furrified — picking up classifier / scheme fixes.
        See PLAN_FURRIFIER_REFURRIFY.md.
        """
        from .facegen import _matches_only_npc
        from .main import _check_cancel

        # Build a map of FormID -> winning record across all plugins.
        # Last occurrence wins (plugins are in load order).
        winning: dict[int, Record] = {}
        for plugin in plugins:
            for npc in plugin.get_records_by_signature('NPC_'):
                winning[record_key(npc)] = npc

        if only_npc is not None:
            winning = {key: npc for key, npc in winning.items()
                       if _matches_only_npc(npc, only_npc)}
            if not winning:
                log.warning(f"--only={only_npc!r} matched no NPC")

        furrifier_names = self.furrifier_patch_names()
        count = 0
        rederived = 0
        preserved = 0
        processed = 0
        total = len(winning)
        for npc in winning.values():
            _check_cancel(cancel_event)
            processed += 1
            if (processed % 500) == 0:
                log.debug(f"  NPCs: {processed}/{total}")

            if is_furrified(self.plugin_set, npc, furrifier_names):
                if preserve_existing:
                    preserved += 1
                    # Keep the existing definition, but remember the
                    # record so FaceGen can still bake it. Skipping it
                    # outright left these NPCs with no nif and no tint.
                    self.preserved_npcs.append(npc)
                    continue
                pre = find_pre_furry_record(
                    self.plugin_set, npc, furrifier_names)
                if pre is None:
                    continue
                rederived += 1
                npc = pre

            result = self.furrify_npc(npc)
            if result is not None:
                count += 1

        if rederived:
            log.info(
                f"Re-derived {rederived} already-furrified NPCs from "
                f"pre-furry overrides")
        if preserved:
            log.info(f"Preserved {preserved} already-furrified NPCs")

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
        # Breeds bypass variant handling — they reuse the parent race's
        # engine identity, so the parent race's variants are what get
        # selected at duplicate time.
        # Pre-compute (rule, base_race, breed_name_or_none) per group,
        # dropping rules whose target isn't a loaded race or breed.
        active_by_group: list[list[tuple]] = []
        for group in groups:
            active_rules: list[tuple] = []
            for rule in group.races:
                breed = self.ctx.breeds.get(rule.race)
                if breed is not None:
                    # Breed: variant comes from the parent race family.
                    if any(v in self.races
                           for v in _variant_names(breed.parent_race_edid)):
                        active_rules.append((rule, breed.parent_race_edid,
                                             rule.race))
                    else:
                        log.warning(
                            f"Leveled NPC breed {rule.race!r}'s parent "
                            f"race {breed.parent_race_edid!r} is not "
                            f"loaded; skipping rule")
                    continue
                base = _strip_variant_suffix(rule.race)
                if any(v in self.races for v in _variant_names(base)):
                    active_rules.append((rule, base, None))
                else:
                    log.warning(
                        f"Leveled NPC race {rule.race!r} is not loaded; "
                        f"skipping rule")
            active_by_group.append(active_rules)
        if not any(active_by_group):
            return (0, 0)

        # Cache of created duplicates: (source NPC key, furry_race) -> Record
        duplicates: dict[tuple[int, str], Record] = {}
        lists_extended = 0

        # Build NPC key -> winning record lookup once
        npc_by_key: dict[int, Record] = {}
        for plugin in plugins:
            for npc in plugin.get_records_by_signature('NPC_'):
                npc_by_key[record_key(npc)] = npc

        # Walk LVLN winning overrides
        winning_lvln: dict[int, Record] = {}
        for plugin in plugins:
            for lvln in plugin.get_records_by_signature('LVLN'):
                winning_lvln[record_key(lvln)] = lvln

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
                src_npc = npc_by_key.get(ref_key(lvln, sr.get_form_id(4)))
                if src_npc is None:
                    continue
                src_key = record_key(src_npc)
                src_race = self.determine_npc_race(src_npc)
                if src_race is None:
                    continue
                source_race_id = src_race[0]
                variant_suffix = _variant_suffix(source_race_id)

                src_alias = unalias(
                    src_npc.editor_id or str(src_npc.form_id))
                for rule, base_race, breed_name in active_rules:
                    # For breeds, override target stays the breed name
                    # — the breed-aware override_furry_race path picks
                    # the parent race for RNAM. For races, append the
                    # source NPC's variant suffix (Vampire/Child).
                    if breed_name is not None:
                        target_race = breed_name
                        engine_race = base_race + variant_suffix
                        if engine_race not in self.races:
                            continue  # parent race variant absent
                    else:
                        target_race = base_race + variant_suffix
                        if target_race not in self.races:
                            continue  # variant not defined for this family

                    if (src_key, target_race) in added_in_this_list:
                        continue

                    decision_key = (
                        f"{lvln.editor_id or ''}:{src_alias}:{base_race}")
                    threshold = int(rule.probability * 1000)
                    if hash_string(decision_key, 7831, 1000) >= threshold:
                        continue

                    cache_key = (src_key, target_race)
                    dup = duplicates.get(cache_key)
                    if dup is None:
                        dup = self._create_leveled_duplicate(
                            src_npc, target_race)
                        if dup is None:
                            continue
                        duplicates[cache_key] = dup

                    dup_norm = dup.normalize_form_id(dup.form_id)
                    new_entries.append((level, count, dup_norm))
                    added_in_this_list.add((src_key, target_race))

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


    def furrify_all_races(self, reuse_existing_subraces: bool = False) -> int:
        """Furrify all vanilla races that have furry assignments.

        Also creates subrace records: copies the vanilla basis race,
        then furrifies the copy with the subrace's furry appearance.

        `reuse_existing_subraces` adopts a subrace record already present
        in the load order instead of minting a fresh one. Set it for an
        additive pass over an earlier furrifier patch (--preserve-existing):
        that patch already created the subraces, and minting duplicates
        would leave two RACE records sharing one EditorID but holding
        different FormIDs. Everything this pass then writes — SOS
        compatible-race lists, sheath ARMA race lists — would point at the
        duplicate while the NPCs still point at the original, so SOS reads
        the schlong as invalid for the actor's race.

        Returns count of races furrified.
        """
        count = 0

        existing_subraces: dict[str, Record] = {}
        if reuse_existing_subraces and self.plugin_set is not None:
            wanted = {s.name for s in self.ctx.subraces.values()}
            # Load order, so the last hit is the winning override.
            for plugin in self.plugin_set:
                for rec in plugin.get_records_by_signature('RACE'):
                    if rec.editor_id in wanted:
                        existing_subraces[rec.editor_id] = rec

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
            adopted = existing_subraces.get(subrace.name)
            if adopted is not None:
                # Already furrified by the patch we're extending. Take its
                # FormID so every reference we write agrees with the NPCs
                # that patch already repointed.
                self.races[subrace.name] = adopted
                log.debug("Reusing existing subrace %s (%08X from %s)",
                          subrace.name,
                          int(adopted.normalize_form_id(adopted.form_id)),
                          adopted.plugin.file_path.name
                          if adopted.plugin.file_path else '?')
                continue

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

            # Subraces are NPC-only variants (Reachmen, Skaal, ...) —
            # the player picks the base race at chargen, so keep them
            # out of the race menu. The basis race we copied is
            # typically playable, so clear the flag explicitly.
            if not _set_race_playable(new_race, False):
                log.warning(
                    f"Subrace {subrace.name}: DATA too short to clear "
                    f"the Playable flag")

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

        # Build NPC key -> Record lookup for preset resolution
        npc_by_key: dict[int, Record] = {}
        for plugin in plugins:
            for rec in plugin.get_records_by_signature('NPC_'):
                npc_by_key[record_key(rec)] = rec  # last wins

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
                    preset_npc = npc_by_key.get(
                        ref_key(furrified_rec, sr.get_form_id()))
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
            # A patch-created subrace carries a local sentinel that
            # normalize_form_id round-trips unchanged; an adopted one
            # (--preserve-existing, record owned by an earlier furrifier
            # patch) needs real normalization out of its own plugin's
            # master space. Taking .value raw was correct only for the
            # first case and wrote a garbage FormID for the second.
            s_fid = subrace_rec.normalize_form_id(subrace_rec.form_id).value
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
        # Include local races (subraces with sentinel FormIDs). Keyed the
        # same way as above so patch-created and adopted subraces both
        # resolve; normalize_form_id is a no-op on the sentinel.
        for edid, rec in self.races.items():
            nfid = rec.normalize_form_id(rec.form_id)
            race_fid_lookup[nfid.value] = nfid

        # Track which FLSTs we've already processed (by normalized FormID)
        processed_flsts: set[int] = set()
        count = 0

        # Walk all HDPT records (winning overrides only)
        winning_hdpts: dict[int, Record] = {}
        for plugin in plugins:
            for rec in plugin.get_records_by_signature('HDPT'):
                winning_hdpts[record_key(rec)] = rec

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

            # Entries that survive the removal pass below. A race already
            # in the list must not be appended again — reachable when the
            # list we're overriding came from an earlier furrifier patch
            # that already added it (--preserve-existing). Checking
            # `new_fids` alone missed it whenever the existing entry sat
            # later in the list than the furry race that triggers the add.
            # Removed entries are deliberately excluded: a furrified
            # vanilla race is dropped here and re-added after its furry
            # race, which is how the list gets reordered.
            kept = {fid for fid in current_fids if fid not in vanilla_to_furry}

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
                        if (furrified_fid not in new_fids
                                and furrified_fid not in kept):
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

    def _build_armor_fallbacks(self) -> dict[int, list[FormID]]:
        """Build fallback race map from furry race RNAM subrecords.

        Each furry race has an RNAM pointing to its "armor race" -- the
        race whose armor meshes fit it. If an ARMA references the armor
        race but not the furry race, we should still add the furrified
        vanilla race.

        Returns: normalized fallback FormID value -> list of normalized
        vanilla FormIDs. Keyed on the normalized (load-order) FormID, NOT
        the bare object index -- two mods routinely place unrelated races
        at the same object index, and collapsing them cross-contaminates
        their armor race lists. See furrify_all_armor.
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
            fb_norm = furry_rec.normalize_form_id(fb_fid).value
            furry_norm = furry_rec.normalize_form_id(furry_rec.form_id).value
            if fb_norm == furry_norm:
                continue
            v_norm = vanilla_rec.normalize_form_id(vanilla_rec.form_id)
            fallbacks.setdefault(fb_norm, []).append(v_norm)
        return fallbacks


    def furrify_all_armor(self, plugins,
                          bodypart_mask: int = FURRIFIABLE_BODYPARTS) -> int:
        """Adjust armor addon race lists driven by ARMO addon order.

        `bodypart_mask` selects which biped slots take part; addons
        outside it are invisible to the whole pass, so they neither
        receive races nor lose them. Defaults to every furrifiable slot.
        See armor_bodypart_mask() for how --armor/--schlongs map onto it.

        After race furrification, vanilla races like NordRace have furry
        head meshes. This method walks each ARMO's ARMA list (MODL refs)
        in order. For each furrified vanilla race, the first ARMA in the
        list that has the corresponding furry race wins -- that ARMA gets
        the furrified vanilla race added. ARMAs that lose the priority
        contest (or have no furry/fallback race) get furrified vanilla
        races removed.

        Must be called after merge_armor_overrides() so the ARMO addon
        lists are complete.

        Everything here is keyed on NORMALIZED (load-order) FormIDs, never
        on bare object indices. Two mods routinely place unrelated records
        at the same object index -- CellanRace (CellanRace.esp) and
        YASKaloRace (BDCatRaces.esp) are both 000800 -- and keying on the
        object index alone silently merges them, so a Cellan ARMA picks up
        the vanilla race mapped to a cat. schlongs.py made the same fix
        earlier for its FLST lookups.

        Returns count of ARMA records modified.
        """
        # normalized furry FormID -> list of (vanilla_norm_value, vanilla_fid)
        furry_to_vanilla: dict[int, list[tuple[int, FormID]]] = {}

        def _pair(furry_id: str, vanilla_id: str) -> None:
            furry_rec = self.races.get(furry_id)
            vanilla_rec = self.races.get(vanilla_id)
            if not (furry_rec and vanilla_rec):
                return
            f_norm = furry_rec.normalize_form_id(furry_rec.form_id).value
            v_fid = vanilla_rec.normalize_form_id(vanilla_rec.form_id)
            furry_to_vanilla.setdefault(f_norm, []).append(
                (v_fid.value, v_fid))

        for a in self.ctx.assignments.values():
            _pair(a.furry_id, a.vanilla_id)
        # Subraces count too. Some furry races have NO direct vanilla
        # assignment and are reachable only through a subrace -- Cellan
        # (Sailor), Bagha (Morag Tong / Dragon Bridge), Deer (Falkreath),
        # Horse (Alikr), Konoi (Reachman), Vaalsark (Skaal). Skipping them
        # here left their ARMAs looking like they carried no furry race at
        # all, so the KhajiitRace fallback below fired and handed them
        # EVERY furrified vanilla race: YASTigerMaleSheathAA_P00 (RNAM
        # YASBaghaRace) ended up with 26 races while
        # YASShanMaleSheathAA_P00 (RNAM YASShanRace, which does have a
        # direct SnowElfRace mapping) correctly had 5. main.py already
        # folds subraces in for the schlong pass; this matches it.
        for sub in self.ctx.subraces.values():
            _pair(sub.furry_id, sub.name)

        furry_fids: set[int] = set(furry_to_vanilla.keys())

        # All furrified vanilla races, as normalized FormID values
        furrified_fids: set[int] = set()
        for a in self.ctx.assignments.values():
            vanilla_rec = self.races.get(a.vanilla_id)
            if vanilla_rec:
                furrified_fids.add(
                    vanilla_rec.normalize_form_id(vanilla_rec.form_id).value)

        # Fallback: normalized fallback FormID -> [(vanilla_norm_value, fid)]
        armor_fallbacks_raw = self._build_armor_fallbacks()
        armor_fallbacks: dict[int, list[tuple[int, FormID]]] = {}
        for fb_norm, v_fids in armor_fallbacks_raw.items():
            armor_fallbacks[fb_norm] = [(fid.value, fid) for fid in v_fids]
        fallback_fids: set[int] = set(armor_fallbacks.keys())

        # Winning ARMA records (last per normalized FormID, including patch)
        winning_armas: dict[int, Record] = {}
        for plugin in plugins:
            for arma in plugin.get_records_by_signature('ARMA'):
                winning_armas[
                    arma.normalize_form_id(arma.form_id).value] = arma
        for arma in self.patch.get_records_by_signature('ARMA'):
            winning_armas[arma.normalize_form_id(arma.form_id).value] = arma

        # Helper: get normalized race FormIDs from an ARMA
        def arma_race_fids(arma_rec):
            fids = set()
            rnam = arma_rec.get_subrecord('RNAM')
            if rnam and rnam.size >= 4:
                fids.add(
                    arma_rec.normalize_form_id(rnam.get_form_id()).value)
            for sr in arma_rec.get_subrecords('MODL'):
                if sr.size >= 4:
                    fids.add(
                        arma_rec.normalize_form_id(sr.get_form_id()).value)
            return fids

        # Race key -> lowercased EditorID, across every plugin plus the
        # patch. Backs the EditorID dedup below; see the comment there for
        # why FormID equality is not enough. Lowercased because
        # scheme-supplied EDIDs have bitten us on case before.
        race_edid_by_key: dict[int, str] = {}
        for plugin in plugins:
            for rec in plugin.get_records_by_signature('RACE'):
                if rec.editor_id:
                    race_edid_by_key[record_key(rec)] = rec.editor_id.lower()
        for rec in self.patch.get_records_by_signature('RACE'):
            if not rec.editor_id:
                continue
            race_edid_by_key[record_key(rec)] = rec.editor_id.lower()
            # Also under the local sentinel. A reference from inside the
            # patch to one of the patch's own records is written as
            # 0xFF|objidx, and normalizes back to that whenever the patch
            # isn't in the load order -- which is the normal state during
            # this pass. Registering only record_key() would leave those
            # references unresolvable here, and the EditorID dedup would
            # silently fall back to comparing FormIDs.
            race_edid_by_key[
                _LOCAL_SENTINEL_HIGH | (rec.form_id.value & 0x00FFFFFF)
            ] = rec.editor_id.lower()

        def arma_race_edids(arma_rec):
            """EditorIDs of the races an ARMA already carries."""
            return {race_edid_by_key[k]
                    for k in arma_race_fids(arma_rec)
                    if k in race_edid_by_key}

        # Walk all ARMO records; for each, resolve the ARMA priority
        # arma normalized FormID -> set of normalized vanilla FormIDs to add
        arma_adds: dict[int, set] = {}
        # arma normalized FormID -> set of vanilla normalized values to remove
        arma_removes: dict[int, set[int]] = {}

        winning_armos: dict[int, Record] = {}
        for plugin in plugins:
            for armo in plugin.get_records_by_signature('ARMO'):
                winning_armos[
                    armo.normalize_form_id(armo.form_id).value] = armo
        # Patch overrides from merge_armor_overrides win over everything
        for armo in self.patch.get_records_by_signature('ARMO'):
            winning_armos[armo.normalize_form_id(armo.form_id).value] = armo

        for armo in winning_armos.values():
            # Get this ARMO's ARMA refs in order
            arma_refs = []
            for sr in armo.get_subrecords('MODL'):
                if sr.size >= 4:
                    arma_fid = armo.normalize_form_id(sr.get_form_id()).value
                    arma_rec = winning_armas.get(arma_fid)
                    if arma_rec:
                        bp = get_bodypart_flags(arma_rec)
                        if bp & bodypart_mask:
                            arma_refs.append((arma_fid, arma_rec))

            if not arma_refs:
                continue

            # For each furrified vanilla race, find the first ARMA in
            # the list that has its furry race (or fallback).
            # Track which ARMA "owns" each vanilla race (claimed globally,
            # and per-ARMA so we know which to keep vs remove).
            claimed: set[int] = set()  # vanilla FormIDs assigned to an ARMA
            # arma FormID -> set of vanilla FormIDs this ARMA owns
            arma_owns: dict[int, set[int]] = {}

            for arma_fid, arma_rec in arma_refs:
                race_fids = arma_race_fids(arma_rec)

                # Which vanilla races can this ARMA claim?
                claimable: list[tuple[int, FormID]] = []

                # Direct furry race matches
                for f_fid in (race_fids & furry_fids):
                    for v_val, v_fid in furry_to_vanilla[f_fid]:
                        if v_val not in claimed:
                            claimable.append((v_val, v_fid))

                # Fallback matches (only if no direct furry)
                if not (race_fids & furry_fids):
                    for fb_fid in (race_fids & fallback_fids):
                        for v_val, v_fid in armor_fallbacks[fb_fid]:
                            if v_val not in claimed:
                                claimable.append((v_val, v_fid))

                if claimable:
                    owns = arma_owns.setdefault(arma_fid, set())
                    adds = arma_adds.setdefault(arma_fid, set())
                    # Dedup by EditorID, not FormID. The races added here
                    # include patch-CREATED subraces, and a previous
                    # generation of the patch normally sits in the load
                    # order carrying its own copy of each: same EditorID,
                    # different patch-local FormID. A raw-FormID "already
                    # present?" test misses that and adds the race a
                    # second time -- YASReachmanRace, YASSkaalRace,
                    # YASWinterholdRace and friends all landed twice on
                    # the Daedric helmet ARMAs. Same fix briarheart.py
                    # already carries.
                    race_edids = arma_race_edids(arma_rec)
                    pending: set[str] = set()
                    for v_val, v_fid in claimable:
                        claimed.add(v_val)
                        owns.add(v_val)
                        edid = race_edid_by_key.get(v_val)
                        if edid is not None:
                            if edid in race_edids or edid in pending:
                                continue
                            pending.add(edid)
                        elif v_val in race_fids:
                            continue
                        adds.add(v_fid)

            # Remove furrified vanilla races from ARMAs that don't
            # own them. Races already present but owned stay; races
            # present but not owned get removed.
            for arma_fid, arma_rec in arma_refs:
                race_fids = arma_race_fids(arma_rec)
                owned = arma_owns.get(arma_fid, set())
                removable = (race_fids & furrified_fids) - owned
                if removable:
                    removes = arma_removes.setdefault(arma_fid, set())
                    removes |= removable

        # Apply changes to ARMA records
        count = 0
        all_affected = set(arma_adds.keys()) | set(arma_removes.keys())
        for arma_fid in all_affected:
            arma_rec = winning_armas.get(arma_fid)
            if arma_rec is None:
                continue

            adds = arma_adds.get(arma_fid, set())
            removes = arma_removes.get(arma_fid, set())
            # Don't remove races that are being added
            removes -= {fid.value for fid in adds}

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
                        norm = patched.normalize_form_id(
                            sr.get_form_id()).value
                        if norm in removes:
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


    @staticmethod
    def _packed_ref_keys(record: Record, sig: str) -> set[int]:
        """Record keys of every FormID in a PACKED-array subrecord (KWDA).

        The old helper read one uint32 per subrecord, so a KWDA holding
        eight keywords reported only the first -- which made the
        "does the winner already have this set?" check permanently
        disagree and rewrite KWDA on every merge.
        """
        keys = set()
        for sr in record.get_subrecords(sig):
            for off in range(0, sr.size - 3, 4):
                fid = FormID(struct.unpack_from('<I', sr.data, off)[0])
                key = ref_key(record, fid)
                if key is not None:
                    keys.add(key)
        return keys


    @staticmethod
    def _merge_addon_order(overrides, tie_break):
        """Merge several overrides' ARMA orderings into one.

        `overrides` is [(load_index, [normalized addon key, ...])] and
        `tie_break` maps an addon key to a sort key (in production, the
        pre-existing mods-first-then-base ordering).

        Each override states a total order over the addons it lists.
        Every ordered pair is a constraint owned by that plugin; a
        later-loading override wins a pair an earlier one ordered
        differently. Pairs no override mentions -- and cycles, when three
        overrides disagree in a rock-paper-scissors -- fall back to
        `tie_break`, so the result is always total and always
        deterministic.

        Base plugins participate like any other; they simply load first
        and so lose every contested pair. That is the point: USSEP is
        authoritative about WHICH addons exist, not about their order,
        and conflating the two is what discarded the race mods'
        deliberate reorderings. See PLAN_FURRIFIER_ARMOR_ORDER.md.

        Addon lists run 3-6 entries, so all-pairs costs nothing.
        """
        # Unordered pair -> (deciding load index, (first, second))
        constraint: dict[frozenset, tuple] = {}
        members: list[int] = []
        seen: set[int] = set()
        for load_index, keys in overrides:
            unique = [k for k in dict.fromkeys(keys)]
            for k in unique:
                if k not in seen:
                    seen.add(k)
                    members.append(k)
            for i, a in enumerate(unique):
                for b in unique[i + 1:]:
                    pair = frozenset((a, b))
                    prev = constraint.get(pair)
                    if prev is None or prev[0] <= load_index:
                        constraint[pair] = (load_index, (a, b))

        # Deterministic tie-break, also the fallback ordering inside a
        # cycle. Ties in tie_break itself resolve by first-seen.
        position = {k: i for i, k in enumerate(members)}
        members.sort(key=lambda k: (tie_break(k), position[k]))
        rank = {k: i for i, k in enumerate(members)}

        edges = {pair_order: load_index
                 for load_index, pair_order in constraint.values()}

        out: list[int] = []
        remaining = set(members)
        while remaining:
            indegree = {k: 0 for k in remaining}
            for a, b in edges:
                if a in remaining and b in remaining:
                    indegree[b] += 1
            ready = [k for k in remaining if indegree[k] == 0]
            if not ready:
                # Contradictory constraints. Drop the lowest-precedence
                # edge still in play -- the earliest-loading claim is the
                # one to give up -- and try again.
                live = {e: w for e, w in edges.items()
                        if e[0] in remaining and e[1] in remaining}
                weakest = min(live, key=lambda e: (live[e], rank[e[0]],
                                                   rank[e[1]]))
                del edges[weakest]
                continue
            pick = min(ready, key=lambda k: rank[k])
            out.append(pick)
            remaining.discard(pick)
        return out


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


    def merge_armor_overrides(
            self, plugins,
            preserve_existing: bool = False,
            bodypart_mask: int = FURRIFIABLE_BODYPARTS) -> int:
        """Merge ARMA references and keywords across ARMO overrides.

        `bodypart_mask` scopes which ARMOs are merged at all: one whose
        furrifiable addons all sit outside the mask is skipped, because
        furrify_all_armor would ignore it anyway and the merged copy
        would be a patch record that changes nothing this run cares
        about. ARMOs carrying no furrifiable addon are always merged --
        that part is load-order conflict resolution, not furrification.
        See armo_in_bodypart_scope().

        When multiple mods override the same ARMO to add their ARMA
        (armor addon) or keywords, only the winning override survives.
        This method merges MODL (ARMA refs) and KWDA (keywords) using:

        1. Find the best base override (USSEP > DLCs > Update > Skyrim)
        2. Start with the base's MODL/KWDA as the authoritative set --
           MEMBERSHIP only; that is what USSEP's removals are about
        3. Add any MODL/KWDA introduced by non-base mod overrides
        4. Merge the MODL ORDER as pairwise constraints across every
           override, later load order winning (_merge_addon_order), so
           furrify_all_armor's priority-by-order sees what the race mods
           actually asked for

        Step 4 used to sort by "mod plugins first, base last", which
        attributed every vanilla addon to the base plugin and so always
        reproduced VANILLA order within that block. A mod that reorders
        vanilla entries -- which both YASCanineRaces and BDUngulates do,
        demoting the human/mer catch-all below the Khajiit addon -- had
        its intent structurally discarded, on 31 ARMOs of a real load
        order. Membership and ordering are now separate concerns.

        `preserve_existing` mirrors the NPC pass. The current run's
        target patch is the one plugin that gets special treatment: when
        preserving we are keeping its choices, so its addon order counts;
        otherwise we are re-deriving and it must not pin this run's
        order, or a bad order outlives the run that produced it. Older,
        differently-named furrifier patches keep their vote either way.

        Returns count of ARMO records merged.
        """
        patch_name = (self.patch.file_path.name.lower()
                      if self.patch.file_path else None)

        def _votes_on_order(rec: Record) -> bool:
            if preserve_existing or patch_name is None:
                return True
            return not (rec.plugin and rec.plugin.file_path
                        and rec.plugin.file_path.name.lower() == patch_name)

        # Build plugin name -> load index for sorting
        plugin_index: dict[str, int] = {}
        for i, plugin in enumerate(plugins):
            if plugin.file_path:
                plugin_index[plugin.file_path.name.lower()] = i
        base_names = set(self._ARMOR_BASE_PRIORITY)

        def _plugin_load_index(src_rec: Record) -> int:
            """Plain load-order position -- the precedence a plugin's
            ordering claims carry. Later loading wins, exactly as it
            does for every other kind of conflict."""
            if src_rec.plugin and src_rec.plugin.file_path:
                return plugin_index.get(
                    src_rec.plugin.file_path.name.lower(), 0)
            return 0

        def _plugin_sort_key(src_rec: Record) -> int:
            """Tie-break for addon pairs no override ever ordered: mod
            plugins by load order, base plugins last. This was the whole
            ordering rule before; it is now only the fallback."""
            if src_rec.plugin and src_rec.plugin.file_path:
                name = src_rec.plugin.file_path.name.lower()
                idx = plugin_index.get(name, 0)
                if name in base_names:
                    # Base plugins sort after all mods
                    return 10000 + idx
                return idx
            return 0

        # Collect all overrides of each ARMO. Keyed on the record key --
        # NOT the object index. Two mods routinely place unrelated ARMOs at
        # the same object index, and grouping on it made them overrides of
        # each other: 97 records in a shipped patch carried a different
        # armor's addons. Same fix furrify_all_armor already carries.
        armo_overrides: dict[int, list[Record]] = {}
        for plugin in plugins:
            for rec in plugin.get_records_by_signature('ARMO'):
                armo_overrides.setdefault(record_key(rec), []).append(rec)

        # ARMA lookup backing the bodypart scope check below. Built only
        # when the mask actually filters -- with the full mask every ARMO
        # is in scope by definition, so resolving addons would be pure
        # cost on the common path.
        scoped = int(bodypart_mask) != int(FURRIFIABLE_BODYPARTS)
        armas_by_key: dict[int, Record] = {}
        if scoped:
            for plugin in plugins:
                for arma in plugin.get_records_by_signature('ARMA'):
                    armas_by_key[record_key(arma)] = arma
            for arma in self.patch.get_records_by_signature('ARMA'):
                armas_by_key[record_key(arma)] = arma

        count = 0
        skipped_out_of_scope = 0
        for overrides in armo_overrides.values():
            if len(overrides) < 2:
                continue

            winner = overrides[-1]
            base = self._find_base_override(overrides)

            # Start with the base's sets as authoritative.
            # Keyed by record key, for the same reason as above.
            merged_modl: dict[int, tuple[FormID, Record]] = {}
            merged_kwda: dict[int, FormID] = {}

            for sr in base.get_subrecords('MODL'):
                if sr.size >= 4:
                    nfid = base.normalize_form_id(sr.get_form_id())
                    merged_modl.setdefault(nfid.value, (nfid, base))
            for sr in base.get_subrecords('KWDA'):
                # KWDA is a packed array — iterate 4 bytes at a time.
                # Null slots are skipped, matching _packed_ref_keys; if
                # only one side dropped them, need_kwda would never agree
                # and every merge would rewrite the keyword list.
                for off in range(0, sr.size - 3, 4):
                    nfid = base.normalize_form_id(
                        FormID(struct.unpack_from('<I', sr.data, off)[0]))
                    if nfid.value:
                        merged_kwda.setdefault(nfid.value, nfid)

            # Add entries from non-base overrides (mods)
            for rec in overrides:
                if rec is base:
                    continue
                if self._is_base_plugin(rec):
                    continue
                for sr in rec.get_subrecords('MODL'):
                    if sr.size >= 4:
                        nfid = rec.normalize_form_id(sr.get_form_id())
                        merged_modl.setdefault(nfid.value, (nfid, rec))
                for sr in rec.get_subrecords('KWDA'):
                    for off in range(0, sr.size - 3, 4):
                        nfid = rec.normalize_form_id(
                            FormID(struct.unpack_from('<I', sr.data, off)[0]))
                        if nfid.value:
                            merged_kwda.setdefault(nfid.value, nfid)

            # Drop ARMOs this run's bodypart mask puts out of scope.
            # Checked against the MERGED addon set, not the winner's:
            # merging is exactly what pulls a sheath addon onto an ARMO
            # that didn't list one, and that is the case a schlongs-only
            # run most needs to catch.
            if scoped and not armo_in_bodypart_scope(
                    merged_modl, armas_by_key, bodypart_mask):
                skipped_out_of_scope += 1
                continue

            # Check if the winner already has the merged set
            winner_modl_list = [
                winner.normalize_form_id(sr.get_form_id()).value
                for sr in winner.get_subrecords('MODL')
                if sr.size >= 4]
            winner_kwda = self._packed_ref_keys(winner, 'KWDA')

            # Order MODL by merging every override's stated order, later
            # load order winning. Restricted to the merged membership --
            # a base-priority override that lost the membership contest
            # (e.g. Dawnguard when USSEP is the base) still gets a vote on
            # the order of the entries that DID survive, but must not
            # reintroduce ones USSEP removed.
            order_sources = []
            for rec in overrides:
                if not _votes_on_order(rec):
                    continue
                keys = [k for k in (
                    rec.normalize_form_id(sr.get_form_id()).value
                    for sr in rec.get_subrecords('MODL') if sr.size >= 4)
                    if k in merged_modl]
                if keys:
                    order_sources.append(
                        (_plugin_load_index(rec), keys))

            def _tie_break(k):
                return _plugin_sort_key(merged_modl[k][1])

            sorted_modl_keys = self._merge_addon_order(
                order_sources, _tie_break)
            # Membership is decided above, ordering here -- so anything
            # the ordering pass didn't see still has to come along. That
            # happens when the only override listing an addon is one that
            # doesn't vote (the run's own target patch under re-derive).
            # Dropping it here would silently REMOVE the addon.
            unordered = [k for k in merged_modl if k not in sorted_modl_keys]
            if unordered:
                sorted_modl_keys = sorted_modl_keys + sorted(
                    unordered, key=_tie_break)
            sorted_modl = [(k, merged_modl[k]) for k in sorted_modl_keys]

            need_modl = (sorted_modl_keys != winner_modl_list)
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
                for _key, (nfid, src_rec) in sorted_modl:
                    sr = patched.add_subrecord('MODL', b'\x00\x00\x00\x00')
                    self.patch.write_form_id(sr, 0, nfid)

            # Replace KWDA with a single subrecord containing all keywords
            if need_kwda:
                patched.remove_subrecords('KWDA')
                kwda_data = bytearray(4 * len(merged_kwda))
                sr = patched.add_subrecord('KWDA', bytes(kwda_data))
                for i, nfid in enumerate(merged_kwda.values()):
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
        if skipped_out_of_scope:
            log.debug("Skipped %d ARMO records outside bodypart mask 0x%X",
                      skipped_out_of_scope, int(bodypart_mask))
        return count

    # -- Statistics --

    def print_statistics(self) -> None:
        """Log post-run summary statistics at DEBUG. The race and hair
        distribution blocks are useful for tuning a scheme but too
        verbose for default INFO output — `--debug` (CLI) / Debug
        checkbox (GUI) surfaces them when wanted."""
        total = sum(self.stats_race_counts.values())
        if total == 0:
            return

        log.debug("")
        log.debug("========== RACE DISTRIBUTION ==========")
        for race_id in sorted(self.stats_race_counts,
                              key=lambda r: -self.stats_race_counts[r]):
            n = self.stats_race_counts[race_id]
            pct = 100 * n / total
            log.debug(f"  {race_id}: {n} ({pct:.1f}%)")
        log.debug(f"  Total: {total}")

        for label, hair_dict in [("MALE", self.stats_hair_male),
                                 ("FEMALE", self.stats_hair_female)]:
            if not hair_dict:
                continue
            hair_total = sum(hair_dict.values())
            log.debug("")
            log.debug(f"========== {label} HAIR DISTRIBUTION ==========")
            for hp_id in sorted(hair_dict,
                                key=lambda h: -hair_dict[h]):
                n = hair_dict[hp_id]
                pct = 100 * n / hair_total
                log.debug(f"  {hp_id}: {n} ({pct:.1f}%)")
            log.debug(f"  Total: {hair_total}")
