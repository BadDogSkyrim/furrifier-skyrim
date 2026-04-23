# Release notes

## v1.1.0 — 2026-04-23

Polish release on top of 1.0.0: CLI ergonomics cleanup, a user-facing
tint-size control, and a camera-reset button in the preview pane.

### New

- **`--facetint-size {256,512,1024,2048,4096}`** — force baked face-tint
  DDSes to a specific square edge length. Defaults to the first resolvable
  mask's native size (vanilla = 512), matching prior behavior. Mirrored
  in the GUI as a "Tint size" dropdown on the options row.
- **Reframe button** on the preview nav row. Resets yaw, pitch, pan, and
  zoom to the scene's default framing when you've orbited off the head.

### Changed

- **CLI flag rename.** `--log-file` → `--log`; `--output-dir` → `-o` /
  `--output`. The old names still work — they're hidden argparse aliases
  that share the same destination — but `--help` only shows the new names.
- **Preview status label.** Initial text now reads `Click 'Load NPCs'
  for preview.` instead of the vaguer "to begin."

### Fixed

- **Compositor no longer double-decodes the first mask.** The canvas-size
  probe at the top of `composite_layers` used to call the full DDS decoder
  just to read each candidate mask's shape; it now reads the image header
  only (lazy `Image.open`), so the first resolvable mask is decoded
  exactly once instead of twice. Small per-NPC win (~0.5 s on the first
  mask in an Auto-sized run), cheap change.

### Known issues

- Aborted runs can leave orphan PNGs in the FaceTint output folder (PNGs
  without a matching DDS). Subsequent runs don't clean them up or
  re-encode them. If you run into it, delete the stale PNGs and re-run.

---

## v1.0.0 — 2026-04-23

First stable release. Ships both a CLI (`furrify_skyrim.exe`) and a GUI
(`furrify_skyrim_gui.exe`) with a live 3D preview pane. Bakes FaceGen nif +
tint DDS per NPC as part of the run, so Creation Kit's Ctrl-F4 step is no
longer required. Scheme and race-catalog TOMLs ship alongside the exe and
can be extended without code changes.
