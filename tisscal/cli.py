"""The command line: list, preview, export, sync."""
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
    print("\nCopy the course numbers you want into `courses = [...]` in config.toml.")


def cmd_preview(events: list[Lecture], cfg: dict) -> None:
    marks = {"exam": "[EXAM]", "registration": "[REGISTER]"}
    for ev in events:
        flag = marks.get(ev.kind) or ("[DEADLINE]" if is_exam(ev, cfg) else "")
        when = ev.start_dt.strftime("%a %d.%m.%y %H:%M")
        # display_title, not ev.summary, so the preview shows the real calendar title
        print(f"{when}  {flag:<11} {display_title(ev, cfg)[:54]:<54} {ev.location[:24]}")
    kinds = defaultdict(int)
    for ev in events:
        kinds[ev.kind] += 1
    summary = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
    print(f"\n{len(events)} events would be synced  ({summary}).")


def cmd_export(events: list[Lecture], cfg: dict, out: Path) -> None:
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
    ap.add_argument("-c", "--config", default="config.toml")
    ap.add_argument("-o", "--out", default="tiss_clean.ics", help="output file for `export`")
    args = ap.parse_args()

    # The project directory, not the package directory: config.toml, token.json and
    # service_account.json all live next to the repository root.
    base = Path(__file__).resolve().parent.parent
    cfg_path = Path(args.config) if Path(args.config).is_absolute() else base / args.config
    cfg = load_config(cfg_path)
    cfg["_tag"] = f"{SOURCE_TAG}-{cfg_path.stem}"  # separate configs never delete each other's events

    events = parse_feed(fetch_feed(cfg["tiss"]["ical_url"]))

    if args.command == "list":
        cmd_list(events)  # unfiltered on purpose, so you see everything
        return

    events = filter_events(events, cfg)
    events = drop_placeholders(events, cfg["placeholder_min_hours"])
    # Before the scraped events are added, so this can never remove a registration
    # reminder - that reminder is the one thing you do want while waiting for a group.
    events = hide_exercises(events, cfg)

    # Exams and registration deadlines are deliberately added after the semester filter:
    # a retake exam or a late registration window often falls outside the lecture window
    # but is exactly what you need reminding about.
    if cfg["scrape"].get("courses"):
        events += scraped_events(cfg)

    # After the scraped events, so an exam held in five rooms at once becomes one event.
    if cfg["merge_parallel_rooms"]:
        events = merge_parallel(events)

    events = prune_past(events, cfg)

    # Several scraped rows can describe the same thing (one window per sitting); they
    # share an id, so collapse them here rather than letting the counts lie.
    events = sorted({e.gcal_id: e for e in events}.values(), key=lambda e: e.start_dt)

    if args.command == "preview":
        cmd_preview(events, cfg)
    elif args.command == "export":
        cmd_export(events, cfg, Path(args.out))
    elif args.command == "sync":
        cmd_sync(events, cfg, base)


if __name__ == "__main__":
    main()
