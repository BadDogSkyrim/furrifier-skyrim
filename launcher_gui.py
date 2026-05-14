"""PyInstaller entry point for the furrifier GUI.

Sibling of launcher.py (the CLI entry point). Lives outside the package so
PyInstaller can run it as a plain script.
"""
import multiprocessing

from furrifier.gui import main


if __name__ == "__main__":
    # Without this, every facegen worker subprocess re-launches the GUI.
    multiprocessing.freeze_support()
    main()
