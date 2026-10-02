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
re-exported below are what cleanup_imported.py, check_cloud_run.py and the tests import.

See tisscal/__init__.py for what lives in which module.
"""
from tisscal.cli import cmd_export, cmd_list, cmd_preview, main
from tisscal.config import load_config
from tisscal.events import scraped_events
from tisscal.feed import fetch_feed, parse_feed
from tisscal.gcal import (RETRY_STATUS, THROTTLE_REASONS, _execute, _gcal_body, cmd_sync)
from tisscal.model import (COURSE_NR, SOURCE_TAG, TYPE_CODES, UID_TIMESTAMP, VIENNA,
                           Lecture)
from tisscal.pipeline import (course_label, display_title, drop_placeholders,
                             filter_events, hide_exercises, is_exam, is_exercise,
                             matches_course, merge_parallel, prune_past)

__all__ = [
    "COURSE_NR", "Lecture", "RETRY_STATUS", "SOURCE_TAG", "THROTTLE_REASONS",
    "TYPE_CODES", "UID_TIMESTAMP", "VIENNA", "_execute", "_gcal_body", "cmd_export",
    "cmd_list", "cmd_preview", "cmd_sync", "course_label", "display_title",
    "drop_placeholders", "fetch_feed", "filter_events", "hide_exercises", "is_exam",
    "is_exercise", "load_config", "main", "matches_course", "merge_parallel",
    "parse_feed", "prune_past", "scraped_events",
]

if __name__ == "__main__":
    main()
