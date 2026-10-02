#!/usr/bin/env python3
"""Remove the hand-imported TISS events from the Google Calendar.

Background: the full TISS feed was once imported into the calendar by hand. Those
events carry no `source` marker, so `tiss_sync.py` cannot see or clean them up - it
only ever touches events it created itself. The result is every lecture showing up
twice, plus the courses you did not select.

This script deletes exactly the events on the configured calendar that have **no**
tiss_sync marker. Everything the sync created is left alone.

  python cleanup_imported.py          # show what would be deleted, change nothing
  python cleanup_imported.py --apply  # actually delete, after writing a backup

The backup is a full JSON dump of every deleted event, so a mistake is recoverable.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from googleapiclient.errors import HttpError

from gcal_auth import get_service
from tiss_sync import _execute, load_config

BACKUP = "deleted_untagged_backup.json"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="perform the deletions")
    ap.add_argument("-c", "--config", default="settings.toml")
    args = ap.parse_args()

    base = Path(__file__).resolve().parent
    settings = load_config(base / args.config)
    svc = get_service(base)
    cal_id = settings.calendar_id

    items: list[dict] = []
    page = None
    while True:
        resp = _execute(svc.events().list(calendarId=cal_id, singleEvents=False,
                                          maxResults=2500, pageToken=page))
        items += resp.get("items", [])
        page = resp.get("nextPageToken")
        if not page:
            break

    def tag(ev: dict) -> str | None:
        return ev.get("extendedProperties", {}).get("private", {}).get("source")

    untagged = [e for e in items if not tag(e)]
    kept = [e for e in items if tag(e)]

    print(f"calendar holds {len(items)} events: "
          f"{len(kept)} created by tiss_sync, {len(untagged)} imported by hand")
    if not untagged:
        print("Nothing to clean up.")
        return

    print("\nimported events to delete, by title:")
    for title, n in sorted(collections.Counter(
            e.get("summary", "(no title)")[:52] for e in untagged).items()):
        print(f"  {n:>4}  {title}")

    if not args.apply:
        print(f"\nDry run. Re-run with --apply to delete these {len(untagged)} events.")
        return

    (base / BACKUP).write_text(json.dumps(untagged, indent=1, ensure_ascii=False),
                               encoding="utf-8")
    print(f"\nBackup of all {len(untagged)} events written to {BACKUP}")

    deleted = already_gone = failed = 0
    for n, ev in enumerate(untagged, 1):
        try:
            _execute(svc.events().delete(calendarId=cal_id, eventId=ev["id"]))
            deleted += 1
        except HttpError as e:
            if e.resp.status in (404, 410):   # already removed
                already_gone += 1
            else:
                failed += 1
                print(f"  FAILED {ev['id']}: {e.resp.status}", file=sys.stderr)
        if n % 60 == 0:
            print(f"  ...{n}/{len(untagged)}")

    print(f"\nDone: {deleted} deleted, {already_gone} already gone, {failed} failed.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
