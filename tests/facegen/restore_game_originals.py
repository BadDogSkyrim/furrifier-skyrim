"""
Restore the game-folder files that our spike outputs overwrote.
Run after finishing in-game testing.

Files in game_originals/ get copied back to their original locations:
  *.NIF  ->  Data\\meshes\\actors\\character\\FaceGenData\\FaceGeom\\Skyrim.esm\\
  *.dds  ->  Data\\textures\\actors\\character\\FaceGenData\\FaceTint\\Skyrim.esm\\
"""
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent
ORIGINALS = HERE / "game_originals"
GAME_DATA = Path(r"C:\Steam\steamapps\common\Skyrim Special Edition\Data")
GAME_FACEGEOM = GAME_DATA / "meshes/actors/character/FaceGenData/FaceGeom/Skyrim.esm"
GAME_FACETINT = GAME_DATA / "textures/actors/character/FaceGenData/FaceTint/Skyrim.esm"


def dest_for(orig: Path) -> Path:
    suffix = orig.suffix.lower()
    if suffix == ".nif":
        return GAME_FACEGEOM / orig.name
    if suffix == ".dds":
        return GAME_FACETINT / orig.name
    raise ValueError(f"no destination for {orig.name}")


def restore() -> int:
    if not ORIGINALS.is_dir():
        print(f"no originals to restore at {ORIGINALS}")
        return 0

    restored = 0
    for orig in sorted(ORIGINALS.iterdir()):
        if not orig.is_file():
            continue
        dst = dest_for(orig)
        shutil.copy2(orig, dst)
        print(f"[restore] {orig.name} -> {dst.parent.name}/ ({orig.stat().st_size} bytes)")
        restored += 1
    print(f"\nrestored {restored} files.")
    return 0


if __name__ == "__main__":
    sys.exit(restore())
