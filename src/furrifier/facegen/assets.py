"""Asset resolver: map Data-relative paths to concrete on-disk paths.

The facegen engine reads source headpart nifs, chargen tri files, and
tint masks by their canonical Data-relative paths (e.g.
`meshes\\actors\\character\\MaleHead.nif`). In the test fixture tree
those are always loose files; for live furrifier runs against a real
install they live inside `Skyrim - Meshes0.bsa` / `Skyrim - Textures.bsa`
and a handful of other archives.

AssetResolver tries loose first, then falls back to scanning every BSA
in the Data directory. BSA-sourced files are extracted once into a
per-run temp directory and the cached path handed to callers, so
PyNifly / PIL can open them by path without changes.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, List, Optional

from esplib.utils import is_readable_file, ensure_dir


log = logging.getLogger("furrifier.facegen.assets")


# Per-process ceiling on the decoded-mask cache. Facegen runs one
# resolver per worker process, so the real cost is this times the
# worker count — 8 workers at the default is ~2 GiB.
_DEFAULT_CACHE_MB = 256


def _cache_budget_bytes() -> int:
    raw = os.environ.get("FURRIFY_MASK_CACHE_MB")
    if raw:
        try:
            mb = int(raw)
            if mb > 0:
                return mb * 1024 * 1024
            log.warning("FURRIFY_MASK_CACHE_MB=%r must be positive; ignoring",
                        raw)
        except ValueError:
            log.warning("FURRIFY_MASK_CACHE_MB=%r is not an int; ignoring", raw)
    return _DEFAULT_CACHE_MB * 1024 * 1024


class BoundedArrayCache:
    """LRU cache of numpy arrays with a byte budget.

    Replaces the plain dict this used to be. Unbounded, it grew by one
    entry per (mask, canvas size) pair for the life of the worker — at a
    2048px canvas that was tens of MiB apiece, and eight workers between
    them exhausted a 64 GiB box mid-run. Every NPC whose composite
    happened to land when memory was tight got skipped outright, with no
    facegen written at all.

    Dict-compatible for the two operations the compositor uses (`get`
    and `__setitem__`) so callers can still hand in a plain dict.

    An array larger than the whole budget is passed through uncached
    rather than evicting everything for a single entry that can't help.
    """

    def __init__(self, max_bytes: Optional[int] = None):
        self.max_bytes = (_cache_budget_bytes() if max_bytes is None
                          else max_bytes)
        self._entries: "OrderedDict[Any, Any]" = OrderedDict()
        self.nbytes = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, key, default=None):
        try:
            value = self._entries[key]
        except KeyError:
            self.misses += 1
            return default
        self._entries.move_to_end(key)
        self.hits += 1
        return value

    def __setitem__(self, key, value) -> None:
        size = int(getattr(value, "nbytes", 0))
        if key in self._entries:
            self.nbytes -= int(getattr(self._entries.pop(key), "nbytes", 0))
        if size > self.max_bytes:
            # Too big to ever be worth holding; hand it back uncached.
            return
        self._entries[key] = value
        self.nbytes += size
        while self.nbytes > self.max_bytes and len(self._entries) > 1:
            _, evicted = self._entries.popitem(last=False)
            self.nbytes -= int(getattr(evicted, "nbytes", 0))
            self.evictions += 1

    def __contains__(self, key) -> bool:
        return key in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()
        self.nbytes = 0


class AssetResolver:
    """Resolve a Data-relative asset path to a concrete file on disk.

    Loose files under `data_dir` win over BSA content, matching the
    game's own precedence rules.

    Typical live use:
        with AssetResolver.for_data_dir(data_dir) as resolver:
            nif_path = resolver.resolve("meshes\\actors\\character\\foo.nif")
            if nif_path is not None:
                nif = NifFile(str(nif_path))

    Tests construct a resolver with an explicit `bsa_readers=[...]` list
    (or an empty list for loose-only scenarios) to avoid depending on a
    real game install.
    """

    def __init__(self, data_dir: Path, bsa_readers: Optional[Iterable] = None,
                 cache_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir)
        self._bsa_readers: List = list(bsa_readers) if bsa_readers is not None else []
        # Cache: relpath-key (backslash, lowercase) -> absolute path on disk.
        self._resolved: dict[str, Path] = {}
        # Directory listings for the case-insensitive loose-file walk:
        # lowercased dir path -> {lowercased entry name: real name}.
        # See _child_named.
        self._dir_cache: dict[str, dict[str, str]] = {}
        # Decoded-image cache piggybacked on the run-scoped resolver.
        # Owned by whoever populates it (currently `composite.py`); the
        # resolver just provides a place to hang it. Many NPCs of the
        # same race share masks, and Pillow's DDS decoder is expensive.
        # Key shape is opaque to the resolver.
        self.image_cache = BoundedArrayCache()
        # Temp dir for BSA extractions. Lazily created on first extract so
        # loose-only runs don't touch the temp filesystem.
        self._cache_dir: Optional[Path] = (
            Path(cache_dir) if cache_dir is not None else None
        )
        self._owns_cache_dir = cache_dir is None

    # ------------------------------------------------------------ factory --

    @classmethod
    def for_data_dir(cls, data_dir: Path) -> "AssetResolver":
        """Scan `data_dir` for *.bsa files, open each, and return a
        resolver wired up with all of them.

        BSAs that fail to parse (wrong version, corrupt header, non-BSA
        content) are logged and skipped — we don't want one broken
        archive in the Data folder to abort a run.
        """
        data_dir = Path(data_dir)
        readers: List = []
        # No is_dir() guard: glob on a non-directory yields nothing
        # anyway, and stat can't be trusted to recognise a directory
        # under MO2's virtual filesystem.
        # Import here so the module-level import graph stays clean
        # for test environments that don't have esplib on sys.path.
        from esplib.bsa import BsaReader, BsaError

        for candidate in sorted(data_dir.glob("*.bsa")):
            try:
                reader = BsaReader(candidate)
                reader.open()
                readers.append(reader)
            except (BsaError, OSError) as exc:
                log.warning("skipping %s: %s", candidate.name, exc)
        return cls(data_dir, bsa_readers=readers)

    # ------------------------------------------------------------ context --

    def __enter__(self) -> "AssetResolver":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """Release BSA handles and remove the temp cache dir (if we
        created it)."""
        for reader in self._bsa_readers:
            try:
                reader.close()
            except Exception as exc:
                log.debug("bsa close failed: %s", exc)
        self._bsa_readers = []

        if self._owns_cache_dir and self._cache_dir is not None and self._cache_dir.exists():
            try:
                shutil.rmtree(self._cache_dir, ignore_errors=True)
            except Exception as exc:
                log.debug("cache cleanup failed: %s", exc)
        self._cache_dir = None

    # ----------------------------------------------------------- resolve --

    def resolve(self, relpath: str) -> Optional[Path]:
        """Return an absolute path for `relpath`, or None if not found.

        `relpath` is a Data-relative path in Bethesda's convention:
        backslash-separated, typically beginning with `meshes\\` or
        `textures\\`. Case is ignored throughout.
        """
        key = relpath.replace("/", "\\").lower()
        cached = self._resolved.get(key)
        if cached is not None:
            return cached

        loose = self._find_loose(relpath)
        if loose is not None:
            self._resolved[key] = loose
            return loose

        extracted = self._extract_from_bsa(relpath)
        if extracted is not None:
            self._resolved[key] = extracted
            return extracted

        return None

    # ------------------------------------------------------------- loose --

    def _find_loose(self, relpath: str) -> Optional[Path]:
        """Case-insensitive loose-file lookup under data_dir.

        Walk segment-by-segment so we don't depend on the Windows
        filesystem's case handling — callers occasionally hit real-world
        cases where the actual file on disk is `Meshes\\actors\\...`
        with a capital M.

        Every step avoids stat. Under Mod Organizer's virtual
        filesystem, directory enumeration shows the merged view of
        vanilla + mods but stat does not, so the old `is_dir()` /
        `exists()` / `is_file()` walk bailed out on the first
        mod-supplied directory and, if it got that far, rejected the
        file it had just found by name. Enumeration finds the entry;
        opening confirms it. See esplib.utils.is_readable_file.
        """
        parts = [p for p in relpath.replace("/", "\\").split("\\") if p]
        if not parts:
            return None
        *dirs, filename = parts

        current = self.data_dir
        for segment in dirs:
            child = self._child_named(current, segment)
            if child is None:
                return None
            current = child

        # Exact-case hit first: saves listing a directory that may hold
        # thousands of entries when the caller already had it right.
        direct = current / filename
        if is_readable_file(direct):
            return direct
        match = self._child_named(current, filename)
        if match is not None and is_readable_file(match):
            return match
        return None

    def _child_named(self, parent: Path, name: str) -> Optional[Path]:
        """Case-insensitive child lookup by directory enumeration.

        Listings are cached per directory: `_find_loose` is called for
        every headpart of every NPC, and re-scanning `Data\\meshes` a
        few thousand times would be its own performance bug. A run
        doesn't add files to the data dir, so the cache can't go stale
        underneath us.
        """
        key = str(parent).lower()
        listing = self._dir_cache.get(key)
        if listing is None:
            listing = {}
            try:
                with os.scandir(parent) as entries:
                    for entry in entries:
                        listing[entry.name.lower()] = entry.name
            except OSError:
                listing = {}
            self._dir_cache[key] = listing
        real = listing.get(name.lower())
        return (parent / real) if real is not None else None

    # --------------------------------------------------------------- bsa --

    def _extract_from_bsa(self, relpath: str) -> Optional[Path]:
        key = relpath.replace("/", "\\")
        for reader in self._bsa_readers:
            if reader.has_file(key):
                data = reader.read_file(key)
                return self._write_cache(relpath, data)
        return None

    def _ensure_cache_dir(self) -> Path:
        if self._cache_dir is None:
            self._cache_dir = Path(tempfile.mkdtemp(prefix="furrifier_facegen_"))
            self._owns_cache_dir = True
        return self._cache_dir

    def _write_cache(self, relpath: str, data: bytes) -> Path:
        cache_dir = self._ensure_cache_dir()
        # Preserve the relative path structure so debugging is sane —
        # the cached file at meshes/actors/character/foo.nif is
        # obviously its loose-path equivalent.
        normalized = relpath.replace("\\", "/")
        out = cache_dir / normalized
        ensure_dir(out.parent)
        out.write_bytes(data)
        return out
