"""The command line: list, preview, export, sync."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from icalendar import Calendar, Event

from .classify import is_exam
from .config import Settings, load_config
from .feed import fetch_feed, parse_feed
from .gcal import cmd_sync
from .model import Lecture
from .pipeline import build_events
from .titles import display_title


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_list(events: list[Lecture]) -> None:
    groups: dict[str, list[Lecture]] = defaultdict(list)
    for ev in events:
        groups[ev.course_nr or f"(no nr) {ev.summary[:50]}"].append(ev)
    print(f"{'Course':<12} {'Events':>6}  {'First':<10}  {'Last':<10}  Title")
    print("-" * 90)
    for key, evs in sorted(groups.items(), key=lambda kv: kv[1][-1].start_dt, reverse=True):
        first, last = evs[0].start_dt.date(), evs[-1].start_dt.date()
        print(f"{key:<12} {len(evs):>6}  {first}  {last}  {evs[0].summary[:50]}")
    print("\nCopy the course numbers you want into `courses = [...]` in settings.toml.")


def cmd_preview(events: list[Lecture], settings: Settings) -> None:
    marks = {"exam": "[EXAM]", "registration": "[REGISTER]"}
    for ev in events:
        flag = marks.get(ev.kind) or ("[DEADLINE]" if is_exam(ev, settings.reminders) else "")
        when = ev.start_dt.strftime("%a %d.%m.%y %H:%M")
        # display_title, not ev.summary, so the preview shows the real calendar title
        title = display_title(ev, settings.titles, settings.exercises)
        print(f"{when}  {flag:<11} {title[:54]:<54} {ev.location[:24]}")
    kinds = defaultdict(int)
    for ev in events:
        kinds[ev.kind] += 1
    summary = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
    print(f"\n{len(events)} events would be synced  ({summary}).")


def cmd_export(events: list[Lecture], out: Path) -> None:
    cal = Calendar()
    cal.add("prodid", "-//tiss_sync//EN")
    cal.add("version", "2.0")
    for ev in events:
        e = Event()
        e.add("uid", ev.gcal_id + "@tiss-sync")
        e.add("summary", ev.summary)
        e.add("dtstart", ev.start)
        e.add("dtend", ev.end)
        if ev.location:
            e.add("location", ev.location)
        if ev.description:
            e.add("description", ev.description)
        cal.add_component(e)
    out.write_bytes(cal.to_ical())
    print(f"Wrote {len(events)} events to {out}")


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["list", "preview", "export", "sync"])
    ap.add_argument("-c", "--config", default="settings.toml",
                    help="settings file; secrets come from .secrets.toml or the environment")
    ap.add_argument("-o", "--out", default="tiss_clean.ics", help="output file for `export`")
    args = ap.parse_args()

    # The project directory, not the package directory: settings.toml, .secrets.toml
    # and service_account.json all live next to the repository root.
    base = Path(__file__).resolve().parent.parent
    cfg_path = Path(args.config) if Path(args.config).is_absolute() else base / args.config
    # The event tag comes from the file's `profile` key, so the TISS and TUWEL
    # settings own separate events and neither can delete the other's.
    settings = load_config(cfg_path)

    events = parse_feed(fetch_feed(settings.ical_url))

    if args.command == "list":
        cmd_list(events)  # unfiltered on purpose, so you see everything
        return

    events = build_events(events, settings)

    if args.command == "preview":
        cmd_preview(events, settings)
    elif args.command == "export":
        cmd_export(events, Path(args.out))
    elif args.command == "sync":
        cmd_sync(events, settings, base)


if __name__ == "__main__":
    main()
