"""Thin CLI wrapper around scout.sources.seed_watchers.seed.

For convenience: `python scripts/seed_watchers.py` from the repo root.
The same logic is exposed via `scout watchers seed`.
"""
from __future__ import annotations

from scout.logging import configure_logging
from scout.sources.seed_watchers import seed

if __name__ == "__main__":
    configure_logging()
    inserted, total = seed()
    print(f"seeded {inserted}/{total} watchers")
