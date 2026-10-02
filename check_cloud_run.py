#!/usr/bin/env python3
"""Prove that a GitHub Actions run really reached the calendar.

A green workflow only tells you the process exited 0 - not that it authenticated, found
the calendar, and wrote to it.

Comparing Google's `updated` timestamps is not enough on its own: Google only bumps them
when an event's content actually changed, and a steady-state sync changes nothing. That
would look identical to a run that never happened.

So this plants a canary instead. It renames one event to something obviously wrong and
remembers the correct title. The sync - whoever runs it - must put the title back, so
finding it restored is proof the run got all the way through to the calendar. If the
cloud never runs, the only consequence is one oddly named event, which any local
`python tiss_sync.py sync` repairs.

  python check_cloud_run.py --canary   # plant it, then trigger the workflow
  python check_cloud_run.py            # after the run: was it repaired?
  python tiss_sync.py sync             # repairs it locally if you abandon the test
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from gcal_auth import get_service, whoami
from tiss_sync import _execute, load_config

STATE = "cloud_test_baseline.json"
CANARY_TITLE = "CANARY - waiting for the cloud sync to repair this"


def snapshot(svc, cal_id: str) -> list[dict]:
    items: list[dict] = []
    page = None
    while True:
        resp = _execute(svc.events().list(calendarId=cal_id, singleEvents=True,
                                          maxResults=2500, pageToken=page))
        items += resp.get("items", [])
        page = resp.get("nextPageToken")
        if not page:
            break
    return items


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--canary", action="store_true",
                    help="plant the canary and record what the title should be")
    ap.add_argument("-c", "--config", default="config.toml")
    args = ap.parse_args()

    base_dir = Path(__file__).resolve().parent
    settings = load_config(base_dir / args.config)
    svc = get_service(base_dir)
    cal_id = settings.calendar_id
    state_path = base_dir / STATE

    items = snapshot(svc, cal_id)
    tags = collections.Counter(
        i.get("extendedProperties", {}).get("private", {}).get("source", "(untagged)")
        for i in items)
    print(f"identity : {whoami(base_dir)}")
    print(f"events   : {len(items)}  {dict(tags)}")

    if args.canary:
        mine = [i for i in items
                if i.get("extendedProperties", {}).get("private", {}).get("source")
                and i.get("summary") != CANARY_TITLE]
        if not mine:
            sys.exit("No tagged events to use - run `python tiss_sync.py sync` first.")
        # The furthest-future event, so a half-finished test cannot confuse this week.
        victim = max(mine, key=lambda i: i["start"].get("dateTime") or i["start"].get("date"))
        state_path.write_text(json.dumps({
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event_id": victim["id"],
            "correct_title": victim["summary"],
            "when": victim["start"].get("dateTime") or victim["start"].get("date"),
            "count": len(items),
        }, indent=1), encoding="utf-8")
        _execute(svc.events().patch(calendarId=cal_id, eventId=victim["id"],
                                    body={"summary": CANARY_TITLE}))
        print(f"\nCanary planted on: {victim['summary']}")
        print(f"  at {victim['start'].get('dateTime') or victim['start'].get('date')}")
        print(f"  its title is now: {CANARY_TITLE}")
        print(f"\nState saved to {STATE}. Now run the workflow:")
        print("  GitHub -> Actions -> TISS calendar sync -> Run workflow")
        print("When it finishes, run this script again with no arguments.")
        return

    if not state_path.exists():
        sys.exit(f"No {STATE} yet - run with --canary first.")
    state = json.loads(state_path.read_text(encoding="utf-8"))

    current = next((i for i in items if i["id"] == state["event_id"]), None)
    print(f"canary   : planted {state['saved_at']} on an event at {state['when']}")
    print(f"           expected title: {state['correct_title']}")

    if current is None:
        print("\nNOT CONFIRMED: the event is gone from the calendar entirely.")
        print("Run `python tiss_sync.py sync` locally to restore it, then retry.")
        sys.exit(1)

    print(f"           title now     : {current['summary']}")
    if current["summary"] == state["correct_title"]:
        print(f"\nCONFIRMED: the title was repaired, and updated is now "
              f"{current['updated']}.")
        print("A sync authenticated, read the feed and wrote to the calendar. If you did")
        print("not sync locally in the meantime, that was the GitHub Actions run.")
        state_path.unlink()
        print(f"({STATE} removed - the test is finished.)")
        return

    print("\nNOT CONFIRMED: still carrying the canary title, so nothing has synced yet.")
    print("Check, in this order:")
    print("  1. Actions tab - is a run listed at all? If not, the workflow never fired.")
    print("  2. Step 'Restore config and credentials from secrets' - a truncated or")
    print("     mangled secret fails there with a json/tomllib error.")
    print("  3. A 404 on the calendar means the service account was never shared on it,")
    print("     or CONFIG_TOML carries a different calendar_id than you expect.")
    print("  4. Still stuck? `python tiss_sync.py sync` locally repairs the canary.")
    sys.exit(1)


if __name__ == "__main__":
    main()
