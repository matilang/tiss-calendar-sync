"""Reading config.toml.

Every option the rest of the package may read is defaulted here, so a missing section is
never an AttributeError halfway through a sync.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"Config not found: {path}\nCopy config.example.toml to config.toml first.")
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    cfg.setdefault("courses", [])
    cfg.setdefault("exclude_keywords", [])
    cfg.setdefault("reminders", {})
    cfg.setdefault("titles", {})
    cfg.setdefault("exercises", {})
    cfg.setdefault("placeholder_min_hours", 4)
    cfg.setdefault("merge_parallel_rooms", True)
    cfg.setdefault("scrape", {})
    cfg.setdefault("retention", {})
    return cfg
