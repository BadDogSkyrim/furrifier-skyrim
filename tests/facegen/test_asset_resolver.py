"""Tests for the AssetResolver — resolves game-relative asset paths to
concrete on-disk paths, extracting from BSAs to a temp cache when the
requested file isn't loose.

Uses a lightweight fake BsaReader (duck-typed) instead of a real BSA,
so tests stay fast and don't require the game install.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from furrifier.facegen.assets import AssetResolver


class _FakeBsa:
    """Duck-typed stand-in for esplib.bsa.BsaReader.

    Only implements the surface AssetResolver actually calls:
    has_file / read_file / close, plus context-manager protocol so it
    can be stashed in a resolver and closed with it.
    """

    def __init__(self, files: dict[str, bytes], name: str = "fake.bsa"):
        # BsaReader keys are backslash-normalized + lowercased; mirror that.
        self._files = {k.replace("/", "\\").lower(): v for k, v in files.items()}
        self._name = name
        self.closed = False

    @property
    def path(self) -> Path:
        return Path(self._name)

    def has_file(self, path: str) -> bool:
        return path.replace("/", "\\").lower() in self._files

    def read_file(self, path: str) -> bytes:
        key = path.replace("/", "\\").lower()
        if key not in self._files:
            raise KeyError(path)
        return self._files[key]

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def data_dir(tmp_path) -> Path:
    d = tmp_path / "Data"
    d.mkdir()
    return d


def test_loose_file_returns_loose_path(data_dir):
    """When the relpath exists as a loose file, resolver returns that
    path without touching BSAs."""
    loose = data_dir / "meshes" / "foo.nif"
    loose.parent.mkdir(parents=True)
    loose.write_bytes(b"loose bytes")

    resolver = AssetResolver(data_dir, bsa_readers=[])
    try:
        got = resolver.resolve("meshes\\foo.nif")
        assert got is not None
        assert got.resolve() == loose.resolve()
        assert got.read_bytes() == b"loose bytes"
    finally:
        resolver.close()


def test_bsa_fallback_extracts_to_cache(data_dir):
    """When loose is missing but a BSA has the file, resolver extracts
    bytes into its cache dir and returns the cache path."""
    bsa = _FakeBsa({"meshes\\bar.nif": b"from bsa"})

    resolver = AssetResolver(data_dir, bsa_readers=[bsa])
    try:
        got = resolver.resolve("meshes\\bar.nif")
        assert got is not None
        assert got.read_bytes() == b"from bsa"
        # Cache path must NOT be under the live Data dir — we don't want
        # to pollute the user's game folder.
        assert data_dir not in got.parents
    finally:
        resolver.close()


def test_missing_everywhere_returns_none(data_dir):
    """Not loose, not in any BSA → None. Callers can decide whether
    that's fatal or a warn-and-skip."""
    resolver = AssetResolver(data_dir, bsa_readers=[_FakeBsa({})])
    try:
        assert resolver.resolve("meshes\\missing.nif") is None
    finally:
        resolver.close()


def test_case_insensitive_lookup(data_dir):
    """Skyrim paths use mixed case inconsistently. A lookup for
    'Meshes\\Foo.NIF' must find the loose file stored as 'meshes/foo.nif'."""
    loose = data_dir / "meshes" / "foo.nif"
    loose.parent.mkdir(parents=True)
    loose.write_bytes(b"ok")

    resolver = AssetResolver(data_dir, bsa_readers=[])
    try:
        got = resolver.resolve("Meshes\\Foo.NIF")
        assert got is not None
        assert got.read_bytes() == b"ok"
    finally:
        resolver.close()


def test_repeat_resolve_is_cached(data_dir):
    """Repeat resolution of the same BSA-sourced path must not re-extract.
    Extraction is cheap but non-zero; a furrification run with thousands
    of NPCs shouldn't hit the BSA thousands of times per unique asset."""
    calls = []

    class CountingBsa(_FakeBsa):
        def read_file(self, path):
            calls.append(path)
            return super().read_file(path)

    bsa = CountingBsa({"meshes\\baz.nif": b"xyz"})
    resolver = AssetResolver(data_dir, bsa_readers=[bsa])
    try:
        p1 = resolver.resolve("meshes\\baz.nif")
        p2 = resolver.resolve("meshes\\baz.nif")
        assert p1 == p2
        assert len(calls) == 1, f"expected one extract, got {len(calls)}"
    finally:
        resolver.close()


def test_close_removes_cache_dir(data_dir):
    """After close(), the temp cache dir should be gone — otherwise a
    long-running GUI session leaks temp space across runs."""
    bsa = _FakeBsa({"meshes\\tmp.nif": b"bytes"})
    resolver = AssetResolver(data_dir, bsa_readers=[bsa])
    cached = resolver.resolve("meshes\\tmp.nif")
    assert cached is not None
    cache_dir = cached.parent
    assert cache_dir.exists()

    resolver.close()
    assert not cache_dir.exists(), (
        f"cache dir {cache_dir} should be removed on close"
    )


def test_close_closes_bsa_readers(data_dir):
    """Resolver owns BSA handles for the run; close() must release them."""
    bsa1 = _FakeBsa({})
    bsa2 = _FakeBsa({})
    resolver = AssetResolver(data_dir, bsa_readers=[bsa1, bsa2])
    resolver.close()
    assert bsa1.closed and bsa2.closed


def test_context_manager(data_dir):
    """`with AssetResolver(...) as r:` should close on exit."""
    bsa = _FakeBsa({})
    with AssetResolver(data_dir, bsa_readers=[bsa]) as resolver:
        assert resolver is not None
    assert bsa.closed


def test_for_data_dir_factory_opens_bsas(tmp_path):
    """AssetResolver.for_data_dir(d) should scan for *.bsa files under d
    and open them as readers. Verifies the auto-wire path the live
    furrifier uses — no caller should need to hand-list BSAs."""
    data_dir = tmp_path / "Data"
    data_dir.mkdir()
    # Create a placeholder .bsa file. We don't need it to parse — just
    # that for_data_dir() discovers it and tries to open it. If parsing
    # fails (placeholder content), for_data_dir() should log and skip,
    # not crash.
    (data_dir / "Bogus.bsa").write_bytes(b"not a real bsa")

    with AssetResolver.for_data_dir(data_dir) as resolver:
        # Still works for loose files even though the bogus bsa was skipped.
        loose = data_dir / "meshes" / "ok.nif"
        loose.parent.mkdir(parents=True)
        loose.write_bytes(b"loose")
        got = resolver.resolve("meshes\\ok.nif")
        assert got is not None
        assert got.read_bytes() == b"loose"
