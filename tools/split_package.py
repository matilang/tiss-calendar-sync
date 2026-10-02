#!/usr/bin/env python3
"""One-off: split tiss_sync.py into the tisscal package.

Moves code by exact source lines taken from the AST rather than retyping it, so the
refactor cannot quietly alter a line. Every top-level function, class and assignment must
land in exactly one module - the script refuses to run if anything is unassigned or
assigned twice, which is what stops a function from being silently dropped.

Scope of that guarantee, learned the hard way: it covers defs, classes and simple
assignments only. Imports, the module docstring and the `if __name__` guard are not
tracked, and the `if __name__` block was in fact dropped on the first run - `python -m
tisscal.cli` imported cleanly and did nothing at all. Anything not in those three
categories has to be written by hand into the headers below and checked by running the
entry points, not assumed.

Run from the project root:  python tools/split_package.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "tiss_sync.py"

# Which top-level names go where, in the order they should appear in the new file.
LAYOUT: dict[str, list[str]] = {
    "model.py": ["VIENNA", "COURSE_NR", "SOURCE_TAG", "UID_TIMESTAMP", "TYPE_CODES",
                 "Lecture"],
    "config.py": ["load_config"],
    "feed.py": ["_to_aware", "fetch_feed", "parse_feed"],
    "pipeline.py": ["matches_course", "filter_events", "drop_placeholders", "prune_past",
                    "is_exercise", "hide_exercises", "course_label", "display_title",
                    "_exercise_code", "merge_parallel", "is_exam"],
    "events.py": ["_synthetic", "_at", "scraped_events"],
    "gcal.py": ["_gcal_body", "RETRY_STATUS", "THROTTLE_REASONS", "_execute", "cmd_sync"],
    "cli.py": ["cmd_list", "cmd_preview", "cmd_export", "main"],
}

HEADERS: dict[str, str] = {
    "model.py": '''"""The event type everything else passes around, plus the constants that define it."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
''',
    "config.py": '''"""Reading config.toml.

Every option the rest of the package may read is defaulted here, so a missing section is
never an AttributeError halfway through a sync.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path
''',
    "feed.py": '''"""Fetching and parsing an iCal feed - TISS, TUWEL, or a local .ics file."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import requests
from icalendar import Calendar

from .model import COURSE_NR, VIENNA, Lecture

# Use the OS certificate store instead of certifi's bundle. Needed when a local
# antivirus / corporate proxy re-signs HTTPS traffic (its root is in the Windows
# store but not in certifi), which otherwise fails with CERTIFICATE_VERIFY_FAILED.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass
''',
    "pipeline.py": '''"""What happens to events between the feed and the calendar.

Pure functions: events in, events out. No network, no Google, no clock except where a
rule is explicitly about "now" - which is what makes this the part worth testing.
"""
from __future__ import annotations

import hashlib
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from .model import TYPE_CODES, UID_TIMESTAMP, VIENNA, Lecture
''',
    "events.py": '''"""Turning scraped course pages into calendar events.

Exams and registration deadlines exist nowhere in the iCal feed, so they are built here
from what tisscal.scrape reads off the course page.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .model import VIENNA, Lecture
from .pipeline import course_label
''',
    "gcal.py": '''"""Talking to Google Calendar: the event body, the retries, and the sync itself."""
from __future__ import annotations

import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import sleep

from .model import VIENNA, Lecture
from .pipeline import display_title, is_exam
''',
    "cli.py": '''"""The command line: list, preview, export, sync."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from icalendar import Calendar, Event

from .config import load_config
from .events import scraped_events
from .feed import fetch_feed, parse_feed
from .gcal import cmd_sync
from .model import SOURCE_TAG, Lecture
from .pipeline import (display_title, drop_placeholders, filter_events, hide_exercises,
                       is_exam, merge_parallel, prune_past)
''',
}


def blocks(source: str) -> dict[str, tuple[int, int]]:
    """name -> (first line, last line), 1-based inclusive, leading comments included."""
    lines = source.splitlines()
    tree = ast.parse(source)
    spans: dict[str, tuple[int, int]] = {}

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names = [node.name]
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        elif isinstance(node, ast.Assign) and all(
                isinstance(t, ast.Name) for t in node.targets):
            names = [t.id for t in node.targets]  # type: ignore[attr-defined]
            start = node.lineno
        else:
            continue

        # Walk up over comment lines and section rules directly above the block.
        i = start - 2
        while i >= 0 and lines[i].lstrip().startswith("#"):
            i -= 1
        start = i + 2
        for name in names:
            spans[name] = (start, node.end_lineno or node.lineno)
    return spans


def main() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    lines = source.splitlines()
    spans = blocks(source)

    wanted = [n for names in LAYOUT.values() for n in names]
    missing = [n for n in wanted if n not in spans]
    if missing:
        sys.exit(f"not found in {SOURCE.name}: {missing}")

    seen: dict[str, str] = {}
    for module, names in LAYOUT.items():
        for name in names:
            if name in seen:
                sys.exit(f"{name} assigned to both {seen[name]} and {module}")
            seen[name] = module

    unassigned = sorted(set(spans) - set(wanted))
    if unassigned:
        sys.exit(f"nothing claims these top-level names: {unassigned}")

    pkg = ROOT / "tisscal"
    pkg.mkdir(exist_ok=True)
    for module, names in LAYOUT.items():
        parts = [HEADERS[module].rstrip() + "\n"]
        for name in names:
            first, last = spans[name]
            parts.append("\n\n" + "\n".join(lines[first - 1:last]).rstrip() + "\n")
        (pkg / module).write_bytes("".join(parts).encode("utf-8"))
        print(f"  {module:<14} {len(names):>2} blocks")

    print(f"\n{len(wanted)} top-level blocks moved, none left over.")


if __name__ == "__main__":
    main()
