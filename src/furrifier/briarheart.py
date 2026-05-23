"""Briarheart armor wiring for YASFurryWorld.

YASFurryWorld carves a heart-shaped hole in the briarheart body mesh
via three replacement ARMAs (YASBriarHeartAA / EmptyAA / BodyAA) and
ARMO overrides that point each briarheart ARMO at the matching new
ARMA. This module enforces two things in the patch:

1. The post-furrification briarheart race (read from
   EncForsworn02BossMagicBretonM01's RNAM) is in each YAS ARMA's
   Additional Races list, so furrified briarhearts can equip them.
   Dedup is by EditorID so a prior YASNPCPatch already carrying its own
   YASReachmanRace ref (different FormID, same name) doesn't trigger a
   second add.

2. The three briarheart ARMOs each carry exactly one MODL pointing at
   their matching YAS ARMA. merge_armor_overrides collects MODL refs
   from both USSEP and YASFurryWorld; the patch needs the USSEP body
   ARMA dropped so only the chest-hole body is active.

Must run after furrify_all_npcs (so the sample NPC's RNAM is already
the furry race) and after merge_armor_overrides (so the briarheart ARMO
entries in the patch have the merged MODL list to fix up).
"""
from __future__ import annotations

import logging

from esplib import Plugin, PluginSet, Record

log = logging.getLogger(__name__)


YAS_FURRY_WORLD_PLUGIN = 'YASFurryWorld.esp'
BRIARHEART_SAMPLE_NPC = 'EncForsworn02BossMagicBretonM01'
BRIARHEART_ARMAS = (
    'YASBriarHeartAA',
    'YASBriarHeartEmptyAA',
    'YASBriarHeartBodyAA',
)
# ARMO EditorID -> ARMA EditorID it should point at exclusively.
BRIARHEART_ARMO_TO_ARMA = {
    'USKPArmorBriarHeartBody': 'YASBriarHeartBodyAA',
    'ArmorBriarHeart':         'YASBriarHeartAA',
    'ArmorBriarHeartEmpty':    'YASBriarHeartEmptyAA',
}


def _plugin_loaded(plugin_set: PluginSet, name: str) -> bool:
    target = name.lower()
    for plugin in plugin_set:
        if plugin.file_path and plugin.file_path.name.lower() == target:
            return True
    return False


def _arma_has_race_edid(arma: Record, target_edid: str,
                        plugin_set: PluginSet) -> bool:
    """True if RNAM or any MODL on `arma` resolves to a RACE with this
    EditorID. Resolving (rather than comparing raw FormIDs) catches
    prior-patch duplicates that live at a different master index but
    name the same race."""
    sigs_to_check = []
    rnam = arma.get_subrecord('RNAM')
    if rnam is not None:
        sigs_to_check.append(rnam)
    sigs_to_check.extend(arma.get_subrecords('MODL'))
    for sr in sigs_to_check:
        if sr.size < 4:
            continue
        rec = plugin_set.resolve_form_id(sr.get_form_id(), arma.plugin)
        if rec is not None and rec.editor_id == target_edid:
            return True
    return False


def _get_or_copy(patch: Plugin, record: Record) -> Record:
    """Return the patch's override of `record`, creating one if absent."""
    norm = record.normalize_form_id(record.form_id)
    local = patch.denormalize_form_id(norm)
    existing = patch.get_record_by_form_id(local)
    if existing is not None:
        return existing
    return patch.copy_record(record, record.plugin)


def patch_briarheart_armor(plugin_set: PluginSet, patch: Plugin) -> int:
    """Apply briarheart-specific ARMO/ARMA fixups for YASFurryWorld.

    Returns count of records modified. No-op (returns 0) if
    YASFurryWorld.esp isn't loaded.
    """
    if not _plugin_loaded(plugin_set, YAS_FURRY_WORLD_PLUGIN):
        log.debug("%s not loaded; skipping briarheart patch",
                  YAS_FURRY_WORLD_PLUGIN)
        return 0

    sample = plugin_set.get_record_by_edid('NPC_', BRIARHEART_SAMPLE_NPC)
    if sample is None:
        log.warning("Briarheart sample NPC %s not found; skipping",
                    BRIARHEART_SAMPLE_NPC)
        return 0
    rnam = sample.get_subrecord('RNAM')
    if rnam is None or rnam.size < 4:
        log.warning("%s has no RNAM; skipping briarheart patch",
                    BRIARHEART_SAMPLE_NPC)
        return 0
    race_norm = sample.normalize_form_id(rnam.get_form_id())
    race_rec = plugin_set.resolve_form_id(rnam.get_form_id(), sample.plugin)
    race_edid = race_rec.editor_id if race_rec is not None else None
    if race_edid is None:
        log.warning("Briarheart race %s has no EditorID; can't dedup",
                    hex(race_norm.value))

    count = 0

    for arma_edid in BRIARHEART_ARMAS:
        arma = plugin_set.get_record_by_edid('ARMA', arma_edid)
        if arma is None:
            log.warning("Briarheart ARMA %s not found; skipping", arma_edid)
            continue
        # Check the winning record before copying so we don't leave an
        # identical-to-master override in the patch when the dedup wins.
        if (race_edid is not None and
                _arma_has_race_edid(arma, race_edid, plugin_set)):
            continue
        patched = _get_or_copy(patch, arma)
        sr = patched.add_subrecord('MODL', b'\x00\x00\x00\x00')
        patch.write_form_id(sr, 0, race_norm)
        log.debug("Added briarheart race %s to %s", race_edid, arma_edid)
        count += 1

    for armo_edid, arma_edid in BRIARHEART_ARMO_TO_ARMA.items():
        armo = plugin_set.get_record_by_edid('ARMO', armo_edid)
        arma = plugin_set.get_record_by_edid('ARMA', arma_edid)
        if armo is None:
            log.warning("Briarheart ARMO %s not found", armo_edid)
            continue
        if arma is None:
            log.warning("Briarheart target ARMA %s not found for %s",
                        arma_edid, armo_edid)
            continue
        patched_armo = _get_or_copy(patch, armo)
        patched_armo.remove_subrecords('MODL')
        arma_norm = arma.normalize_form_id(arma.form_id)
        sr = patched_armo.add_subrecord('MODL', b'\x00\x00\x00\x00')
        patch.write_form_id(sr, 0, arma_norm)
        log.debug("Set %s MODL to %s only", armo_edid, arma_edid)
        count += 1

    log.info("Briarheart patch modified %d record(s)", count)
    return count
