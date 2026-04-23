"""Stage a baked facegen nif's textures into its temp-tree sibling so
NifSkope (and any other Bethesda tool) can resolve them.

The bake writes the nif at
  <temp>/meshes/actors/character/FaceGenData/FaceGeom/<plugin>/<formid>.nif
and its per-NPC FaceTint DDS at
  <temp>/textures/actors/character/FaceGenData/FaceTint/<plugin>/<formid>.dds

Everything else (race skin diffuse, normal, hair textures, etc.) lives
either loose in the game's Data folder or inside a BSA. If NifSkope
opens the nif and walks up looking for a Data root, it'll find the
temp root — but only the FaceTint DDS lives there. Every other texture
slot resolves to nothing.

`stage_nif_textures` walks the nif, pulls each referenced texture
path out of every shape's shader, resolves it via AssetResolver
(loose + BSA fallback), and copies the result into the corresponding
`<temp>/<relpath>`. After this the temp tree is self-contained.
"""
from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path
from typing import Set

# PyNifly package __init__ imports bpy; bypass it.
_PYNIFLY_DEV = r"C:\Modding\PyNifly\io_scene_nifly"
if _PYNIFLY_DEV not in sys.path:
    sys.path.insert(0, _PYNIFLY_DEV)

from ..facegen import AssetResolver


log = logging.getLogger("furrifier.preview.staging")


def _normalize_relpath(relpath: str) -> str:
    """NIFs store texture paths with backslashes. Pick the OS
    separator-free form for hashing and filesystem ops."""
    return relpath.lstrip("\\/").replace("\\", "/").lower()


def rewrite_textures_absolute(nif_path: Path, temp_root: Path) -> int:
    """Rewrite every shape's shader texture slots from Data-relative
    paths to absolute paths pointing into `temp_root`.

    NifSkope's relative-path resolution doesn't reliably detect an
    arbitrary folder as "Data root" when that folder isn't in its
    configured list — which it isn't for our preview temp dir. With
    absolute paths the resolver is unambiguous.

    Expects :func:`stage_nif_textures` to have run first so the
    absolute paths actually point at real files. Slots whose target
    doesn't exist under temp_root keep the original relative path
    (so a user's NifSkope Data config can still cover them).

    Returns the number of slots rewritten.
    """
    from pyn.pynifly import NifFile

    nif = NifFile(str(nif_path))
    rewritten = 0
    for shape in nif.shapes:
        changed_any = False
        for slot_name, rel in list(shape.textures.items()):
            if not rel:
                continue
            normalized = rel.lstrip("\\/").replace("\\", "/")
            if not normalized.lower().startswith("textures/"):
                normalized = "textures/" + normalized
            abs_path = (temp_root / normalized).resolve()
            if not abs_path.is_file():
                continue  # nothing staged — leave relative.
            shape.set_texture(slot_name, str(abs_path))
            rewritten += 1
            changed_any = True
        if changed_any:
            shape.save_shader_attributes()
    nif.save()
    return rewritten


def stage_nif_textures(nif_path: Path, resolver: AssetResolver,
                       temp_root: Path) -> int:
    """Copy every texture referenced by `nif_path`'s shader slots
    into `temp_root` at the path the slot references.

    The AssetResolver covers loose + BSA; it's expected to be set up
    against the game's Data folder so vanilla + YAS assets both
    resolve. Already-staged textures (e.g. the FaceTint DDS the bake
    emitted) are skipped via mtime check.

    Returns the number of texture files staged this call.
    """
    from pyn.pynifly import NifFile

    nif = NifFile(str(nif_path))
    staged = 0
    seen: Set[str] = set()

    for shape in nif.shapes:
        for slot_name, slot_path in shape.textures.items():
            if not slot_path:
                continue
            key = _normalize_relpath(slot_path)
            if key in seen:
                continue
            seen.add(key)

            # Normalize to textures-relative; if the nif path already
            # starts with "textures/", leave it, otherwise prepend.
            if not key.startswith("textures/"):
                key = "textures/" + key

            target = temp_root / key
            if target.exists() and target.stat().st_size > 0:
                continue  # already staged (e.g. FaceTint from bake)

            src = resolver.resolve(key)
            if src is None:
                log.debug("texture missing, skipping: %s", key)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copyfile(src, target)
                staged += 1
            except OSError as exc:
                log.warning("stage copy failed for %s: %s", key, exc)

    return staged
