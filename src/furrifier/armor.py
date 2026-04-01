"""Armor addon furrification.

Ensures furrified NPCs can equip armor by adding furry races to the
Additional Races list on ARMA records.
Ported from BDFurryArmorFixup.pas.
"""

from __future__ import annotations

import logging
import struct
from typing import Optional

from esplib import Plugin, Record, FormID

from .models import Bodypart

log = logging.getLogger(__name__)

# Bodypart flags that indicate armor needing furry race support
FURRIFIABLE_BODYPARTS = (
    Bodypart.HEAD | Bodypart.HAIR | Bodypart.HANDS |
    Bodypart.LONGHAIR | Bodypart.CIRCLET
)


def get_bodypart_flags(arma: Record) -> int:
    """Get bodypart flags from an ARMA's BOD2 subrecord."""
    bod2 = arma.get_subrecord('BOD2')
    if bod2 and bod2.size >= 4:
        return bod2.get_uint32(0)
    # Fall back to old BODT format
    bodt = arma.get_subrecord('BODT')
    if bodt and bodt.size >= 4:
        return bodt.get_uint32(0)
    return 0


def arma_has_race(arma: Record, target_race_fid: int) -> bool:
    """Check if an ARMA record supports a given race (by FormID).

    Checks both the primary RNAM race and the Additional Races (MODL) list.
    """
    # Check primary race
    rnam = arma.get_subrecord('RNAM')
    if rnam and rnam.get_uint32() == target_race_fid:
        return True

    # Check additional races
    for sr in arma.get_subrecords('MODL'):
        if sr.get_uint32() == target_race_fid:
            return True

    return False


def add_race_to_arma(arma: Record, patch: Plugin, source_plugin: Plugin,
                     race_fid: int) -> Record:
    """Add a race to an ARMA's Additional Races list.

    Creates an override in the patch if one doesn't exist yet.
    Returns the patched ARMA record.
    """
    if arma_has_race(arma, race_fid):
        return arma

    patched = patch.copy_record(arma, source_plugin)
    patched.add_subrecord('MODL', struct.pack('<I', race_fid))
    log.debug(f"Added race {race_fid:#010x} to {arma.editor_id}")
    return patched


def furrify_all_armor(plugins,
                      patch: Plugin,
                      race_mappings: dict[int, int],
                      ) -> int:
    """Add furry races to all armor addons that support their vanilla equivalents.

    race_mappings: dict of vanilla_race_fid -> furry_race_fid

    Returns count of ARMA records modified.
    """
    count = 0

    for plugin in plugins:
        for arma in plugin.get_records_by_signature('ARMA'):
            bp_flags = get_bodypart_flags(arma)
            if not (bp_flags & FURRIFIABLE_BODYPARTS):
                continue

            for vanilla_fid, furry_fid in race_mappings.items():
                if arma_has_race(arma, vanilla_fid) and not arma_has_race(arma, furry_fid):
                    add_race_to_arma(arma, patch, plugin, furry_fid)
                    count += 1
                    break  # Only need to modify once per ARMA

    log.info(f"Modified {count} armor addon records")
    return count
