# Release notes

## v1.2.0 — 2026-04-26

Bug fix on the install path, faster + better-looking face tints, a
sharper preview pane, and a few user-facing polish items.

### Fixed

- **"No module named pyn" on clean kit installs.** The kit didn't
  bundle PyNifly; only Hugh's dev machine had it. Now bundled
  alongside `NiflyDLL.dll` so any unzipped kit just works.

### New / changed

- **In-process BC7 face-tint encoding.** Dropped `tools/texconv.exe`
  from the kit (~1 MB smaller). Face tints encode in-process, no PNG
  round-trip, no subprocess spawn. Slightly higher quality than the
  old texconv pipeline (RMS error 0.21 vs 0.25 against CK reference
  on a real face).

- **Preview pane closer to in-game rendering.** Three improvements:
  the head's diffuse alpha is preserved through the tint composite
  (eye sockets, nostrils, neck seam now carve out correctly); each
  shape uses its own alpha mode from the NIF (hair / scars / hairlines
  blend smoothly instead of getting binary-cut); skin tint is composited
  with Soft Light (was Overlay — too punchy).

- **Session cache shared between Preview and Run.** Loading NPCs in
  the preview pane populates a cache; the Run button reuses it
  instead of reloading every plugin from scratch.

- **Custom schemes.** Drop any `*.toml` into `schemes/` and it
  becomes a valid `--scheme` value (and shows up in the GUI combo).
  Documented in the README "Schemes" section.

- **Per-race TOML files for ungulates and Cellan.** `yas_minorace.toml`,
  `yas_deerrace.toml`, `yas_horserace.toml`, `yas_cellanrace.toml`
  ship alongside `yas_races.toml` so editing one race's headparts
  + probability + labels lives in one place. Drop in your own
  per-race files the same way.

- **Schlongs (SOS) compatibility option** is documented more clearly
  in the README and the `--no-schlongs` flag description; it's a
  furrifier-side toggle, not an SOS-side toggle.

- **README Troubleshooting section.** Covers the most common install
  problems (Mod Organizer 2 launch failures, Windows SmartScreen
  warnings, scheme edits not taking effect).

### Known issues

- *None new.* The v1.1.0 orphan-PNG issue is gone — the in-process
  BC7 path doesn't write PNGs at all.

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
