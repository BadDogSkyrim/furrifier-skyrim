# Release notes

## v1.1.0 — 2026-04-23

Polish release on top of 1.0.0: CLI ergonomics cleanup, a user-facing
tint-size control, and a camera-reset button in the preview pane.

### New

- First stable release. Ships both a CLI (`furrify_skyrim.exe`) and a GUI
(`furrify_skyrim_gui.exe`) with a live 3D preview pane. Bakes FaceGen nif +
tint DDS per NPC as part of the run, so Creation Kit's Ctrl-F4 step is no
longer required. Scheme and race-catalog TOMLs ship alongside the exe and
can be extended without code changes.

- **Tint size option:** `--facetint-size {256,512,1024,2048,4096}` — force baked face-tint
DDSes to a specific size. Defaults to the first resolvable
mask's native size (vanilla = 512), matching prior behavior. Mirrored
in the GUI as a "Tint size" dropdown on the options row.

- **Reframe button** on the preview nav row. Resets yaw, pitch, pan, and
zoom to the scene's default framing when you've orbited off the head.

### Known issues

- Aborted runs can leave orphan PNGs in the FaceTint output folder (PNGs
  without a matching DDS). Subsequent runs don't clean them up or
  re-encode them. If you run into it, delete the stale PNGs and re-run.
