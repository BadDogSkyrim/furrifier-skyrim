"""Which record the preview bakes when a furry patch is left loaded.

The preview has to agree with what a Run would produce:

- Skip-furry-NPCs OFF: re-derive the already-furry NPC under the current
  scheme (show the NEW definition).
- Skip-furry-NPCs ON: show the EXISTING furry NPC untouched.

Before this, an already-furry NPC fell into the plain furrify path,
where determine_npc_race can't match a furry RNAM — so the preview
refused with "scheme doesn't furrify this NPC" whichever way the setting
was pointed.
"""

from types import SimpleNamespace

import pytest

from furrifier.config import FurrifierConfig
from furrifier.preview.worker import PreviewWorker


class _Plugin:
    def __init__(self, name):
        self.file_path = SimpleNamespace(name=name)


class _Rec:
    def __init__(self, edid, plugin):
        self.editor_id = edid
        self.plugin = plugin


@pytest.fixture
def worker(monkeypatch):
    """A PreviewWorker with the session pieces stubbed to the shapes
    _resolve_preview_record touches."""
    w = PreviewWorker(cache=None)

    patch_plugin = _Plugin("YASNPCPatch.esp")
    furry_rec = _Rec("LeifWayfinder", _Plugin("YASNSFWPatch.esp"))
    vanilla_rec = _Rec("LeifWayfinder", _Plugin("Skyrim.esm"))
    furrified = {"furry": True}

    calls = {"furrify": []}

    def fake_furrify(npc):
        calls["furrify"].append(npc)
        return _Rec(f"{npc.editor_id}(refurrified)", patch_plugin)

    ctx = SimpleNamespace(
        furrifier_patch_names=lambda: {"yasnsfwpatch.esp"},
        furrify_npc=fake_furrify,
    )
    w._session = SimpleNamespace(
        patch=patch_plugin, plugin_set=object(), context=ctx,
        config=FurrifierConfig())

    monkeypatch.setattr("furrifier.context.is_furrified",
                        lambda ps, npc, names: npc is furry_rec)
    monkeypatch.setattr("furrifier.context.find_pre_furry_record",
                        lambda ps, npc, names: vanilla_rec)

    return w, furry_rec, vanilla_rec, patch_plugin, calls, furrified


def test_refurrify_shows_the_new_definition(worker):
    """Setting OFF: re-derive from the topmost non-furry record."""
    w, furry_rec, vanilla_rec, _patch, calls, _ = worker
    w._config = FurrifierConfig(preserve_existing=False)

    result = w._resolve_preview_record(furry_rec)

    assert calls["furrify"] == [vanilla_rec], \
        "should re-derive from the non-furry record, not the furry one"
    assert result.editor_id == "LeifWayfinder(refurrified)"


def test_preserve_shows_the_existing_furry_npc(worker):
    """Setting ON: hand back the existing record untouched."""
    w, furry_rec, _vanilla, _patch, calls, _ = worker
    w._config = FurrifierConfig(preserve_existing=True)

    result = w._resolve_preview_record(furry_rec)

    assert result is furry_rec
    assert calls["furrify"] == [], "must not re-furrify when preserving"


def test_record_already_in_our_patch_is_used_as_is(worker):
    w, _furry, _vanilla, patch_plugin, calls, _ = worker
    w._config = FurrifierConfig()
    own = _Rec("Someone", patch_plugin)

    assert w._resolve_preview_record(own) is own
    assert calls["furrify"] == []


def test_non_furry_npc_is_furrified_normally(worker):
    w, _furry, vanilla_rec, _patch, calls, _ = worker
    w._config = FurrifierConfig()

    result = w._resolve_preview_record(vanilla_rec)

    assert calls["furrify"] == [vanilla_rec]
    assert result.editor_id == "LeifWayfinder(refurrified)"


def test_live_config_wins_over_the_cached_session(worker):
    """preserve_existing isn't in the session cache key, so a cached
    session carries a stale copy. Toggling the checkbox must still take
    effect without rebuilding the session."""
    w, furry_rec, _vanilla, _patch, calls, _ = worker
    w._session.config = FurrifierConfig(preserve_existing=False)
    w._config = FurrifierConfig(preserve_existing=True)

    assert w._resolve_preview_record(furry_rec) is furry_rec
    assert calls["furrify"] == []
