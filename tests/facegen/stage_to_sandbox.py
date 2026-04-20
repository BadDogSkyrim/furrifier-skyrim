"""Copy the test suite's generated nif + dds into Hugh's Vortex Sandbox
mod, so in-game rendering reflects the latest output.

Run AFTER the facegen tests so out_headparts/ and out_tints/ are up to
date. Tests should not stage — this belongs outside the test framework.

Usage:
    python stage_to_sandbox.py                    # all 3 vanilla NPCs
    python stage_to_sandbox.py 0001327C           # just Dervenin
"""
import shutil
import sys
from pathlib import Path


HERE = Path(__file__).parent
OUT_NIFS = HERE / "out_headparts" / "Data_vanilla"
OUT_DDS = HERE / "out_tints" / "Data_vanilla"

SANDBOX = Path(r"C:\Users\hughr\AppData\Roaming\Vortex\skyrimse\mods\Sandbox")
SANDBOX_NIF = SANDBOX / "meshes/actors/character/FaceGenData/FaceGeom/Skyrim.esm"
SANDBOX_DDS = SANDBOX / "textures/actors/character/FaceGenData/FaceTint/Skyrim.esm"

DEFAULT_FORMIDS = ["0001414D", "0001327C", "00013268"]  # ulfric, dervenin, deeja


def stage(form_id: str) -> None:
    our_nif = OUT_NIFS / f"{form_id}.nif"
    our_dds = OUT_DDS / f"{form_id}.dds"
    if not our_nif.is_file():
        print(f"  [miss] {our_nif} — run pytest first")
        return
    SANDBOX_NIF.mkdir(parents=True, exist_ok=True)
    SANDBOX_DDS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(our_nif, SANDBOX_NIF / f"{form_id}.NIF")
    if our_dds.is_file():
        shutil.copy2(our_dds, SANDBOX_DDS / f"{form_id}.dds")
    print(f"  [stage] {form_id}")


if __name__ == "__main__":
    ids = sys.argv[1:] or DEFAULT_FORMIDS
    for fid in ids:
        stage(fid)
    print(f"\nStaged to: {SANDBOX}")
