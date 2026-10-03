#!/usr/bin/env python3
"""
tiss_sync.py - entry point. The code now lives in the tisscal package; this file keeps
the commands you already use working unchanged.

  python tiss_sync.py list      # show every course found in the feed -> pick yours
  python tiss_sync.py preview   # show what would be synced, nothing is written
  python tiss_sync.py export    # write a cleaned .ics file (import it manually)
  python tiss_sync.py sync      # create/update/delete events in Google Calendar

Equivalent to `python -m tisscal.cli`. Kept as a file because the scheduled task, the
GitHub Actions workflow and sync_daily.cmd all invoke it by name, and because the names
re-exported below are what check_cloud_run.py, tools/ and the tests import.

See tisscal/__init__.py for what lives in which module.
"""
from tisscal.cli import cmd_export, cmd_list, cmd_preview, main
from tisscal.config import (ConfigError, Exercises, Reminders, Retention, Scrape,
                            Semester, Settings, load_config)
from tisscal.events import scraped_events
from tisscal.feed import fetch_feed, parse_feed
from tisscal.gcal import (RETRY_STATUS, THROTTLE_REASONS, _execute, _gcal_body, cmd_sync)
from tisscal.model import COURSE_NR, TYPE_CODES, UID_TIMESTAMP, VIENNA, Lecture
from tisscal.classify import is_exam, is_exercise
from tisscal.filters import (drop_placeholders, filter_events, hide_exercises,
                             matches_course, prune_past)
from tisscal.merge import merge_parallel
from tisscal.pipeline import build_events
from tisscal.plan import Change, Plan, compare, diff, event_bodies
from tisscal.settings_io import WriteError
from tisscal.settings_io import apply as apply_settings
from tisscal.settings_io import differences as settings_differences
from tisscal.settings_io import write as write_settings
from tisscal.titles import course_label, display_title

__all__ = [
    "COURSE_NR", "Change", "ConfigError", "Exercises", "Lecture", "Plan", "RETRY_STATUS",
    "WriteError", "apply_settings", "settings_differences", "write_settings",
    "Reminders", "compare", "diff", "event_bodies",
    "Retention", "Scrape", "Semester", "Settings", "THROTTLE_REASONS", "build_events",
    "TYPE_CODES", "UID_TIMESTAMP", "VIENNA", "_execute", "_gcal_body", "cmd_export",
    "cmd_list", "cmd_preview", "cmd_sync", "course_label", "display_title",
    "drop_placeholders", "fetch_feed", "filter_events", "hide_exercises", "is_exam",
    "is_exercise", "load_config", "main", "matches_course", "merge_parallel",
    "parse_feed", "prune_past", "scraped_events",
]

if __name__ == "__main__":
    main()
