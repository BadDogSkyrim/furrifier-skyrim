"""SOS (Schlongs of Skyrim) compatibility.

Finds SOS addon quests and adds furry races to their compatible race lists.
Ported from BDFurrifySchlongs.pas.

This is a stub — SOS integration requires parsing VMAD (Papyrus script data)
subrecords to find SOS_AddonQuest_Script properties, which is complex.
The full implementation will be added when needed.
"""

from __future__ import annotations

import logging
from typing import Optional

from esplib import Plugin, Record
from esplib import flst_add, flst_contains, glob_value, glob_set_value, glob_copy_as

log = logging.getLogger(__name__)


def furrify_all_schlongs(plugins: list[Plugin],
                         patch: Plugin,
                         race_fids: list[int],
                         ) -> int:
    """Add furry races to SOS addon race lists.

    This is a simplified version that looks for FLST records containing
    race references and adds the furry races to them. The full Pascal
    implementation parses VMAD script properties on QUST records.

    Returns count of records modified.
    """
    log.info("SOS furrification not yet implemented (requires VMAD parsing)")
    # TODO: Implement VMAD parsing to find SOS_AddonQuest_Script properties
    # For now, this is a no-op stub
    return 0
