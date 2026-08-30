"""
Entry point for `python -m src.cli`.

Required because converting cli.py into a package (src/cli/) means Python
no longer runs the module body directly on -m invocation -- it looks for
this file specifically. Without it, `python -m src.cli` would fail.
"""

from src.cli.parser import main

if __name__ == "__main__":
    main()
