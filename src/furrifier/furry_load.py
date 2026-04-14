"""Setup and data loading from game plugins.

Loads vanilla races, headparts, and tint data from Skyrim plugins using esplib,
then populates RaceInfo objects for furrification.
Ported from BDFurrySkyrimSetup.pas (the plugin-loading parts).
"""

from __future__ import annotations

import logging
from typing import Optional

from esplib import Record

from .models import (
    Sex, HeadpartType, TintLayer, RaceInfo, HeadpartInfo, TintAsset,
)
from .race_defs import RaceDefContext

log = logging.getLogger(__name__)


def is_npc_female(npc: Record) -> bool:
    """Check if an NPC is female from the ACBS flags."""
    try:
        acbs = npc['ACBS']
    except KeyError:
        return False
    if acbs is None:
        return False
    return bool(acbs['flags'].Female)


def is_child_race(race: Record) -> bool:
    """Check if a race is a child race from the DATA flags."""
    data = race.get_subrecord('DATA')
    if data is None or data.size < 36:
        return False
    # The child flag is in the race DATA flags at offset 32 (uint32), bit 2
    flags = data.get_uint32(32)
    return bool(flags & 4)


def get_headpart_type(hdpt: Record) -> HeadpartType:
    """Get headpart type from PNAM subrecord."""
    pnam = hdpt.get_subrecord('PNAM')
    if pnam is None:
        return HeadpartType.UNKNOWN
    val = pnam.get_uint32()
    try:
        return HeadpartType(val)
    except ValueError:
        return HeadpartType.UNKNOWN


def load_races(plugins, ctx: RaceDefContext) -> dict[str, RaceInfo]:
    """Load all races referenced in the context from the plugin set.

    Returns a dict of EditorID -> RaceInfo for all vanilla and furry races.
    """
    races: dict[str, RaceInfo] = {}

    # Collect all race EditorIDs we need
    needed = set()
    for assignment in ctx.assignments.values():
        needed.add(assignment.vanilla_id)
        needed.add(assignment.furry_id)
    for subrace in ctx.subraces.values():
        needed.add(subrace.vanilla_basis)
        needed.add(subrace.furry_id)

    # Find records in the plugins
    for plugin in plugins:
        for record in plugin.get_records_by_signature('RACE'):
            edid = record.editor_id
            if edid and edid in needed:
                races[edid] = RaceInfo(  # last wins = winning override
                    record=record,
                    editor_id=edid,
                    is_child=is_child_race(record),
                )

    log.info(f"Loaded {len(races)} race records")

    # Link assignments to their RaceInfo and warn about missing races
    skip_assignments = []
    for key, assignment in ctx.assignments.items():
        assignment.vanilla = races.get(assignment.vanilla_id)
        assignment.furry = races.get(assignment.furry_id)
        if assignment.vanilla is None:
            log.warning(
                f"Vanilla race not found: {assignment.vanilla_id}"
                f" — skipping assignment")
            skip_assignments.append(key)
        elif assignment.furry is None:
            log.warning(
                f"Furry race not found: {assignment.furry_id}"
                f" — skipping assignment {assignment.vanilla_id}"
                f" -> {assignment.furry_id}")
            skip_assignments.append(key)
    for key in skip_assignments:
        del ctx.assignments[key]

    skip_subraces = []
    for key, subrace in ctx.subraces.items():
        if subrace.vanilla_basis not in races:
            log.warning(
                f"Vanilla basis race not found: {subrace.vanilla_basis}"
                f" — skipping subrace {subrace.name}")
            skip_subraces.append(key)
        elif subrace.furry_id not in races:
            log.warning(
                f"Furry race not found: {subrace.furry_id}"
                f" — skipping subrace {subrace.name}")
            skip_subraces.append(key)
    for key in skip_subraces:
        del ctx.subraces[key]

    return races


def load_headparts(plugins,
                   ctx: RaceDefContext) -> dict[str, HeadpartInfo]:
    """Load all HDPT records and attach labels from the context."""
    headparts: dict[str, HeadpartInfo] = {}

    for plugin in plugins:
        for record in plugin.get_records_by_signature('HDPT'):
            edid = record.editor_id
            if edid is None:
                continue
            hp_type = get_headpart_type(record)
            labels = ctx.headpart_labels.get(edid, [])
            equivalents = ctx.headpart_equivalents.get(edid, [])
            headparts[edid] = HeadpartInfo(
                record=record,
                editor_id=edid,
                hp_type=hp_type,
                labels=list(labels),
                equivalents=list(equivalents),
            )

    log.info(f"Loaded {len(headparts)} headpart records")
    return headparts


def build_race_headparts(plugins,
                         all_headparts: dict[str, HeadpartInfo],
                         ) -> dict[tuple, set[str]]:
    """Build an index of headparts available per (type, sex, race).

    Returns a dict mapping (HeadpartType, sex_int, race_edid) to a set
    of headpart EditorIDs.

    Each HDPT record has:
    - PNAM: headpart type
    - DATA: flags byte (bit 0 = male, bit 2 = female)
    - RNAM: FormID → FormList (FLST) of valid races
    """
    # Build a normalized FormID → EditorID lookup for all RACE records
    race_fid_to_edid: dict[int, str] = {}
    for plugin in plugins:
        for record in plugin.get_records_by_signature('RACE'):
            edid = record.editor_id
            if edid:
                norm = record.normalize_form_id(record.form_id).value
                race_fid_to_edid[norm] = edid

    # Build a normalized FormID → FLST record lookup
    flst_by_fid: dict[int, Record] = {}
    for plugin in plugins:
        for record in plugin.get_records_by_signature('FLST'):
            norm = record.normalize_form_id(record.form_id).value
            flst_by_fid[norm] = record

    race_headparts: dict[tuple, set[str]] = {}

    for hp in all_headparts.values():
        if hp.record is None:
            continue

        # Get DATA flags for sex filtering
        data_sr = hp.record.get_subrecord('DATA')
        if data_sr is None or data_sr.size < 1:
            continue
        flags = data_sr.data[0]
        is_male = bool(flags & 0x02)    # bit 1
        is_female = bool(flags & 0x04)  # bit 2

        # Get RNAM → FormList (normalize through the HDPT's plugin)
        rnam = hp.record.get_subrecord('RNAM')
        if rnam is None:
            continue
        hp_plugin = hp.record.plugin
        rnam_norm = hp_plugin.normalize_form_id(rnam.get_form_id()).value

        flst = flst_by_fid.get(rnam_norm)
        if flst is None:
            continue

        # Get races from the FormList's LNAM entries (normalize through
        # the FLST's plugin)
        flst_plugin = flst.plugin
        race_edids = set()
        for lnam in flst.get_subrecords('LNAM'):
            lnam_norm = flst_plugin.normalize_form_id(
                lnam.get_form_id()).value
            edid = race_fid_to_edid.get(lnam_norm)
            if edid:
                race_edids.add(edid)

        # Insert into index for each applicable sex and race
        sexes = []
        if is_male:
            sexes.extend([Sex.MALE_ADULT, Sex.MALE_CHILD])
        if is_female:
            sexes.extend([Sex.FEMALE_ADULT, Sex.FEMALE_CHILD])

        for sex in sexes:
            for race_edid in race_edids:
                key = (hp.hp_type, sex, race_edid)
                if key not in race_headparts:
                    race_headparts[key] = set()
                race_headparts[key].add(hp.editor_id)

    log.info(f"Built race_headparts index: {len(race_headparts)} entries")
    return race_headparts


# -- Tint class name resolution from file paths --

_TINT_PATH_KEYWORDS = [
    ('SkinTone', 'Skin Tone'),
    ('CheekLower', 'Cheek Color Lower'),
    ('CheekUpper', 'Cheek Color'),
    ('Cheek', 'Cheek Color'),
    ('Chin', 'Chin'),
    ('EyeLower', 'EyeSocket Lower'),
    ('EyeUpper', 'EyeSocket Upper'),
    ('EyeSocket', 'EyeSocket Lower'),
    ('Eyeliner', 'Eyeliner'),
    ('EyeLiner', 'Eyeliner'),
    ('Forehead', 'Forehead'),
    ('ForeHead', 'Forehead'),
    ('LaughLines', 'Laugh Lines'),
    ('FrownLines', 'Laugh Lines'),
    ('LipColor', 'Lip Color'),
    ('Lips', 'Lip Color'),
    ('Neck', 'Neck'),
    ('Nose', 'Nose'),
    # Mustache/Moustache must come before Muzzle so they don't accidentally
    # fall into the Muzzle class (they live in their own class because they
    # are always-applied overlays, not muzzle alternatives).
    ('Mustache', 'Mustache'),
    ('Moustache', 'Mustache'),
    ('Muzzle', 'Muzzle'),
    ('Stripes', 'Stripes'),
    ('SkinTint', 'Skin Tone'),
    ('Spots', 'Spots'),
    ('Stripe', 'Stripes'),
    ('Mask', 'Mask'),
    ('EyebrowSpot', 'Brow'),
    ('EyeSpot', 'Brow'),
    ('Brow', 'Brow'),
    ('Ears', 'Ear'),
    ('Ear', 'Ear'),
    ('BlackBlood', 'BlackBlood'),
    ('Bothiah', 'Bothiah'),
    ('Forsworn', 'Forsworn'),
    ('Frekles', 'Frekles'),
    ('Freckle', 'Frekles'),
    ('NordWarPaint', 'NordWarPaint'),
    ('DarkElfWarPaint', 'DarkElfWarPaint'),
    ('ImperialWarPaint', 'ImperialWarPaint'),
    ('OrcWarPaint', 'OrcWarPaint'),
    ('RedguardWarPaint', 'RedguardWarPaint'),
    ('WoodElfWarPaint', 'WoodElfWarPaint'),
    ('wolfpawprint', 'Wolfpawprint'),
    ('pawprint', 'Wolfpawprint'),
    ('Skull', 'Skull'),
    ('WarPaint', 'Paint'),
    ('warpaint', 'Paint'),
    ('paint', 'Paint'),
    ('Dirt', 'Dirt'),
    ('dirt', 'Dirt'),
]


def _classify_tint_path(path: str) -> str:
    """Determine tint class name from a texture filename."""
    # Use filename only — directory names like "TintMasks" would
    # false-match keywords like "Mask"
    from pathlib import PurePosixPath
    p = PurePosixPath(path.replace('\\', '/'))
    filename = p.name.lower()
    stem = p.stem.lower()

    # "Old" aging tints: filename stem ends with "Old" as a separate word
    # (e.g. KygarraFemOld.dds) but not "Bold", "Gold", etc.
    if p.stem.endswith('Old') or p.stem.endswith('_old'):
        return 'Old'

    for keyword, class_name in _TINT_PATH_KEYWORDS:
        if keyword.lower() in filename:
            return class_name

    return 'Paint'  # fallback for unrecognized paths


def build_race_tints(plugins) -> dict[tuple, 'RaceTintData']:
    """Build tint data for all races, keyed by (race_edid, sex).

    Walks each RACE record's Head Data tint masks and extracts
    TintAsset entries organized by class name.
    """
    import struct
    from .tints import RaceTintData
    from .models import TintAsset

    result: dict[tuple, RaceTintData] = {}

    for plugin in plugins:
        for record in plugin.get_records_by_signature('RACE'):
            edid = record.editor_id
            if not edid:
                continue

            for sex, sex_enum in [(Sex.MALE_ADULT, Sex.MALE_ADULT),
                                  (Sex.FEMALE_ADULT, Sex.FEMALE_ADULT)]:
                tint_data = _extract_tint_section(record, sex)
                if tint_data.classes:
                    result[(edid, sex)] = tint_data
                    # Child races share parent tint data
                    child_sex = Sex.MALE_CHILD if sex == Sex.MALE_ADULT else Sex.FEMALE_CHILD
                    result[(edid, child_sex)] = tint_data

    log.info(f"Built race_tints: {len(result)} entries")
    return result


def _extract_tint_section(record: Record, sex: Sex) -> 'RaceTintData':
    """Extract tint data for one sex from a RACE record's Head Data."""
    import struct
    from .tints import RaceTintData
    from .models import TintAsset

    data = RaceTintData()

    # Find the correct Head Data section:
    # Male: first NAM0 → MNAM → ... → second NAM0
    # Female: second NAM0 → FNAM → ... → end
    is_male = sex in (Sex.MALE_ADULT, Sex.MALE_CHILD)
    target_marker = 'MNAM' if is_male else 'FNAM'

    # Find the start of our section
    in_section = False
    nam0_count = 0
    tint_start = -1

    for i, sr in enumerate(record.subrecords):
        if sr.signature == 'NAM0':
            nam0_count += 1
            if is_male and nam0_count == 1:
                in_section = True
            elif not is_male and nam0_count == 2:
                in_section = True
            elif in_section:
                break  # hit the next NAM0, we're done
        if in_section and sr.signature == 'TINI' and tint_start < 0:
            tint_start = i

    if tint_start < 0:
        return data

    # Parse tint entries: TINI, TINT, TINP, TIND, [TINC, TINV, TIRS]*
    i = tint_start
    subs = record.subrecords

    while i < len(subs):
        sr = subs[i]
        if sr.signature == 'NAM0':
            break  # hit next section

        if sr.signature != 'TINI':
            i += 1
            continue

        tini = struct.unpack('<H', sr.data[:2])[0]
        tint_path = ''
        tinp_val = -1
        presets = []

        j = i + 1
        while j < len(subs) and subs[j].signature != 'TINI' and subs[j].signature != 'NAM0':
            s = subs[j]
            if s.signature == 'TINT':
                tint_path = s.data.decode('cp1252', errors='replace').rstrip('\x00')
            elif s.signature == 'TINP':
                tinp_val = struct.unpack('<H', s.data[:2])[0]
            elif s.signature == 'TINC':
                color_fid = struct.unpack('<I', s.data[:4])[0]
                # Look ahead for TINV and TIRS
                intensity = 0.0
                tirs = 0
                if j + 1 < len(subs) and subs[j + 1].signature == 'TINV':
                    intensity = struct.unpack('<f', subs[j + 1].data[:4])[0]
                if j + 2 < len(subs) and subs[j + 2].signature == 'TIRS':
                    tirs = struct.unpack('<H', subs[j + 2].data[:2])[0]
                presets.append((color_fid, intensity, tirs))
            j += 1

        class_name = _classify_tint_path(tint_path)

        asset = TintAsset(
            index=tini,
            filename=tint_path,
            layer_type=0,
            layer_class=class_name,
            presets=presets,
        )

        if class_name not in data.classes:
            data.classes[class_name] = []
        data.classes[class_name].append(asset)

        # Determine if required: Skin Tone always, or first preset
        # intensity > 0.01
        if class_name == 'Skin Tone':
            data.required.add(class_name)
        elif presets and presets[0][1] > 0.01:
            data.required.add(class_name)

        i = j

    return data
