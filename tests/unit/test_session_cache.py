"""Tests for `SessionCache` — shared plugin/session cache between the
live-preview path and the full Run path.

Uses mocks for `load_plugins` / `build_session_over_plugins` so these
stay pure-unit (no Skyrim install needed). The point is to verify the
cache-key logic, not the actual load work.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from furrifier.config import FurrifierConfig
from furrifier.session_cache import (
    SessionCache,
    plugin_cache_key,
    session_cache_key,
)


def _stub_loaded_plugins(label: str = "plugins"):
    """Return a LoadedPlugins-shaped stub. We never read from it inside
    the cache, so a MagicMock is enough."""
    m = MagicMock(name=label)
    return m


def _stub_session(label: str = "session"):
    return MagicMock(name=label)


@pytest.fixture
def base_config():
    return FurrifierConfig(
        patch_filename="P.esp",
        race_scheme="all_races",
        game_data_dir="C:/game/Data",
        output_dir="C:/mods/out",
    )


class TestCacheKeys:
    def test_plugin_key_ignores_scheme(self, base_config):
        a = plugin_cache_key(base_config, None)
        other = FurrifierConfig(
            patch_filename="P.esp",
            race_scheme="legacy",
            game_data_dir="C:/game/Data",
            output_dir="C:/mods/out",
        )
        b = plugin_cache_key(other, None)
        assert a == b, "scheme changes must not invalidate the plugin load"

    def test_plugin_key_reacts_to_patch_filename(self, base_config):
        a = plugin_cache_key(base_config, None)
        other = FurrifierConfig(
            patch_filename="Different.esp",
            race_scheme="all_races",
            game_data_dir="C:/game/Data",
            output_dir="C:/mods/out",
        )
        b = plugin_cache_key(other, None)
        assert a != b

    def test_session_key_reacts_to_scheme(self, base_config):
        a = session_cache_key(base_config)
        other = FurrifierConfig(
            patch_filename="P.esp",
            race_scheme="legacy",
            game_data_dir="C:/game/Data",
            output_dir="C:/mods/out",
        )
        b = session_cache_key(other)
        assert a != b


class TestGetOrLoadPlugins:
    def test_caches_on_identical_key(self, monkeypatch, base_config):
        plugins = _stub_loaded_plugins()
        call_count = 0

        def fake_load(config, load_order=None, progress=None):
            nonlocal call_count
            call_count += 1
            return plugins

        from furrifier import session_cache
        monkeypatch.setattr(session_cache, "load_plugins", fake_load)

        cache = SessionCache()
        first = cache.get_or_load_plugins(base_config, None)
        second = cache.get_or_load_plugins(base_config, None)
        assert first is plugins
        assert second is plugins
        assert call_count == 1, "same key → one real load_plugins call"


    def test_reloads_on_key_change(self, monkeypatch, base_config):
        call_count = 0
        returned = []

        def fake_load(config, load_order=None, progress=None):
            nonlocal call_count
            call_count += 1
            p = _stub_loaded_plugins(f"plugins{call_count}")
            returned.append(p)
            return p

        from furrifier import session_cache
        monkeypatch.setattr(session_cache, "load_plugins", fake_load)

        cache = SessionCache()
        cache.get_or_load_plugins(base_config, None)
        other = FurrifierConfig(
            patch_filename="Different.esp",
            race_scheme="all_races",
            game_data_dir="C:/game/Data",
            output_dir="C:/mods/out",
        )
        cache.get_or_load_plugins(other, None)
        assert call_count == 2


class TestGetOrBuildSession:
    def test_scheme_only_change_reuses_plugins(self, monkeypatch, base_config):
        plugin_loads = 0
        session_builds = 0

        def fake_load(config, load_order=None, progress=None):
            nonlocal plugin_loads
            plugin_loads += 1
            return _stub_loaded_plugins()

        def fake_build(config, plugins, progress=None):
            nonlocal session_builds
            session_builds += 1
            return _stub_session(f"session{session_builds}")

        from furrifier import session_cache
        monkeypatch.setattr(session_cache, "load_plugins", fake_load)
        monkeypatch.setattr(session_cache, "build_session_over_plugins",
                            fake_build)

        cache = SessionCache()
        cache.get_or_build_session(base_config, None)
        # Change scheme only. Plugin cache must survive; session rebuilds.
        scheme_changed = FurrifierConfig(
            patch_filename="P.esp",
            race_scheme="legacy",
            game_data_dir="C:/game/Data",
            output_dir="C:/mods/out",
        )
        cache.get_or_build_session(scheme_changed, None)

        assert plugin_loads == 1, (
            f"scheme-only change should not reload plugins "
            f"(got {plugin_loads} loads)")
        assert session_builds == 2, (
            f"scheme change should rebuild session "
            f"(got {session_builds} builds)")


    def test_identical_config_reuses_both(self, monkeypatch, base_config):
        plugin_loads = 0
        session_builds = 0

        def fake_load(*a, **kw):
            nonlocal plugin_loads
            plugin_loads += 1
            return _stub_loaded_plugins()

        def fake_build(*a, **kw):
            nonlocal session_builds
            session_builds += 1
            return _stub_session()

        from furrifier import session_cache
        monkeypatch.setattr(session_cache, "load_plugins", fake_load)
        monkeypatch.setattr(session_cache, "build_session_over_plugins",
                            fake_build)

        cache = SessionCache()
        cache.get_or_build_session(base_config, None)
        cache.get_or_build_session(base_config, None)

        assert plugin_loads == 1
        assert session_builds == 1


    def test_plugin_key_change_rebuilds_both(self, monkeypatch, base_config):
        plugin_loads = 0
        session_builds = 0

        def fake_load(*a, **kw):
            nonlocal plugin_loads
            plugin_loads += 1
            return _stub_loaded_plugins()

        def fake_build(*a, **kw):
            nonlocal session_builds
            session_builds += 1
            return _stub_session()

        from furrifier import session_cache
        monkeypatch.setattr(session_cache, "load_plugins", fake_load)
        monkeypatch.setattr(session_cache, "build_session_over_plugins",
                            fake_build)

        cache = SessionCache()
        cache.get_or_build_session(base_config, None)
        other = FurrifierConfig(
            patch_filename="Different.esp",
            race_scheme="all_races",
            game_data_dir="C:/game/Data",
            output_dir="C:/mods/out",
        )
        cache.get_or_build_session(other, None)

        assert plugin_loads == 2
        assert session_builds == 2


class TestInvalidate:
    def test_clears_both(self, monkeypatch, base_config):
        plugin_loads = 0
        session_builds = 0

        def fake_load(*a, **kw):
            nonlocal plugin_loads
            plugin_loads += 1
            return _stub_loaded_plugins()

        def fake_build(*a, **kw):
            nonlocal session_builds
            session_builds += 1
            return _stub_session()

        from furrifier import session_cache
        monkeypatch.setattr(session_cache, "load_plugins", fake_load)
        monkeypatch.setattr(session_cache, "build_session_over_plugins",
                            fake_build)

        cache = SessionCache()
        cache.get_or_build_session(base_config, None)
        cache.invalidate()
        cache.get_or_build_session(base_config, None)

        assert plugin_loads == 2, (
            f"invalidate should force plugin reload (got {plugin_loads})")
        assert session_builds == 2
