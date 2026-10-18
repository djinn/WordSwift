"""Console entrypoint: ``python -m src``.

This is the canonical way to start the server. It exists because
``python -m src.api`` executes api.py's module body twice — once when
``src/__init__.py`` imports it as ``src.api``, and again when runpy executes it
as ``__main__``. Each execution builds an ``IndexRegistry``, which opens the
LevelDB directories and takes their exclusive locks, so the second one silently
falls back to empty in-memory storage and serves a blank index.

Importing ``src.api`` here (rather than re-executing it) means the module body
runs exactly once, and ``run_server`` reuses the registry that already holds the
locks.

``python -m src.api`` is also tolerated: api.py detects the duplicate execution
and delegates to the canonical module.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    # Imported inside main() so that `--help` does not construct a registry, and
    # so argument parsing errors surface before any LevelDB directory is opened.
    from src.api import _main

    _main(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
