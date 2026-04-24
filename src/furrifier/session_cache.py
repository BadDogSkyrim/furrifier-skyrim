"""Shared plugin + session cache between the live preview and the
full Run path.

Both paths need the same expensive plumbing: parse the load order
(~15-20s), build the race context (~1-2s), produce a
:class:`FurrificationSession` over the result. Without this cache,
a user who previews one NPC and then clicks Run pays the plugin-load
cost twice. With it, the second path reuses what the first already
loaded, gated by a pair of config fingerprints.

- **Plugin cache**: scheme-independent. Reload triggers: patch
  filename, game data dir, or plugin-selection fingerprint changed.
- **Session cache**: plugin cache + scheme + patch filename + data/
  output dirs. Scheme-only changes reuse plugins and just rebuild
  the cheap scheme-dependent pieces.

Access is serialized via a lock — preview and run are on separate
QThreads, and while the GUI funnels them to run sequentially in
practice, a race on the cache fields would corrupt state. Loads
happen under the lock: a second caller blocks until the first
finishes and then picks up the cached result.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from esplib import LoadOrder

from .config import FurrifierConfig
from .session import (
    FurrificationSession,
    LoadedPlugins,
    build_session_over_plugins,
    load_plugins,
)


ProgressCallback = Callable[[str], None]


def plugin_cache_key(config: FurrifierConfig,
                     load_order: Optional[LoadOrder]) -> tuple:
    """Fingerprint that invalidates the cached plugin load.

    Plugin loading is scheme-independent, but the user's plugin
    selection (via the main-window picker) does matter — different
    set of plugins means different override chains. Load-order
    fingerprint (tuple of names) goes into the key so toggling
    plugins re-loads; leaving selection alone keeps the cache.
    """
    lo_fingerprint: tuple = ()
    if load_order is not None:
        lo_fingerprint = tuple(p.lower() for p in load_order.plugins)
    return (
        config.patch_filename,
        config.game_data_dir or "",
        lo_fingerprint,
    )


def session_cache_key(config: FurrifierConfig) -> tuple:
    """Fingerprint that invalidates a fully-built session when any
    scheme-dependent field changes. Options like ``furrify_armor``
    don't affect the session — they're consumed later."""
    return (
        config.race_scheme,
        config.patch_filename,
        config.game_data_dir or "",
        config.output_dir or "",
    )


class SessionCache:
    """Shared cache of ``LoadedPlugins`` + ``FurrificationSession``.

    One instance per `FurrifierWindow`. Both the preview worker and
    the run worker receive the same instance and consult it before
    doing any load work.
    """


    def __init__(self) -> None:
        self._plugins: Optional[LoadedPlugins] = None
        self._plugins_key: Optional[tuple] = None
        self._session: Optional[FurrificationSession] = None
        self._session_key: Optional[tuple] = None
        self._lock = threading.Lock()


    def get_or_load_plugins(
            self,
            config: FurrifierConfig,
            load_order: Optional[LoadOrder],
            progress: Optional[ProgressCallback] = None,
    ) -> LoadedPlugins:
        """Return the cached ``LoadedPlugins`` if it still matches the
        config + load-order fingerprint; otherwise run a fresh load."""
        key = plugin_cache_key(config, load_order)
        with self._lock:
            if self._plugins is not None and self._plugins_key == key:
                return self._plugins
            self._plugins = load_plugins(
                config, load_order=load_order, progress=progress)
            self._plugins_key = key
            # New plugin load invalidates any downstream session — its
            # patch was built over the old plugin_set.
            self._session = None
            self._session_key = None
            return self._plugins


    def get_or_build_session(
            self,
            config: FurrifierConfig,
            load_order: Optional[LoadOrder],
            progress: Optional[ProgressCallback] = None,
    ) -> FurrificationSession:
        """Return the cached session if it matches; else reload or
        rebuild as cheaply as possible (scheme-only change keeps the
        plugin cache)."""
        with self._lock:
            plugin_key = plugin_cache_key(config, load_order)
            if self._plugins is None or self._plugins_key != plugin_key:
                self._plugins = load_plugins(
                    config, load_order=load_order, progress=progress)
                self._plugins_key = plugin_key
                self._session = None
                self._session_key = None

            full_key = (plugin_key, session_cache_key(config))
            if self._session is not None and self._session_key == full_key:
                return self._session

            self._session = build_session_over_plugins(
                config, self._plugins, progress=progress)
            self._session_key = full_key
            return self._session


    def invalidate(self) -> None:
        """Drop both plugin and session caches.

        Called after a full Run completes — the Run injects its patch
        into the shared ``plugin_set`` and populates the patch with
        thousands of records, so a subsequent preview needs fresh
        plugins to avoid seeing Run's leftovers in the override chain.
        """
        with self._lock:
            self._plugins = None
            self._plugins_key = None
            self._session = None
            self._session_key = None
