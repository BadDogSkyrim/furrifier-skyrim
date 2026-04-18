"""PyInstaller entry point for the furrifier GUI.

Sibling of launcher.py (the CLI entry point). Lives outside the package so
PyInstaller can run it as a plain script.
"""
from furrifier.gui import main


if __name__ == "__main__":
    main()
