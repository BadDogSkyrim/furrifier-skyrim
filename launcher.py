"""PyInstaller entry point for the furrifier CLI.

This lives outside the package so PyInstaller can run it as a plain script.
The installed console script (`furrifier = "furrifier.main:main"` in
pyproject.toml) is still the preferred way to run from a dev checkout.
"""
import multiprocessing

from furrifier.main import main


if __name__ == "__main__":
    # Without this, every facegen worker subprocess re-enters main() and
    # forks bombs the system. Frozen Windows builds use spawn semantics.
    multiprocessing.freeze_support()
    main()
