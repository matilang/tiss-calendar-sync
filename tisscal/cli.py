"""The command line: list, preview, export, sync."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from icalendar import Calendar, Event

from .classify import is_exam
from .config import ConfigError, Settings, from_dict, load_config
from .feed import fetch_feed, parse_feed
from .gcal import cmd_sync
from .model import Lecture
from .pipeline import build_events
from .plan import compare
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
def _diff_settings(ref: str, cfg_path: Path) -> Settings:
    """The settings to compare against: another .toml file, or a git revision of this one.

    Defaults to HEAD because of how this project runs: the daily sync uses the settings as
    committed, so "what do my uncommitted edits do?" is the question that matters, and it
    needs no second file to answer.

    Parsed without resolving secrets. A diff never fetches anything, so the feed URL and
    calendar id are irrelevant here - and requiring them would make `--diff HEAD` fail on
    a file that quite correctly does not contain them.
    """
    import subprocess
    import tomllib

    path = Path(ref)
    if path.suffix == ".toml":
        if not path.exists():
            raise SystemExit(f"No such settings file: {ref}")
        text, profile = path.read_text(encoding="utf-8"), path.stem
    else:
        done = subprocess.run(["git", "show", f"{ref}:{cfg_path.name}"],
                              cwd=cfg_path.parent, capture_output=True,
                              text=True, encoding="utf-8")
        if done.returncode != 0:
            raise SystemExit(f"Cannot read {cfg_path.name} at {ref}: "
                             f"{done.stderr.strip() or 'not a git working copy?'}")
        text, profile = done.stdout, cfg_path.stem

    try:
        return from_dict(tomllib.loads(text), profile=profile)
    except (ConfigError, tomllib.TOMLDecodeError) as e:
        raise SystemExit(f"{ref}: {e}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["list", "preview", "export", "sync"])
    ap.add_argument("-c", "--config", default="settings.toml",
                    help="settings file; secrets come from .secrets.toml or the environment")
    ap.add_argument("-o", "--out", default="tiss_clean.ics", help="output file for `export`")
    ap.add_argument("--diff", nargs="?", const="HEAD", metavar="REF",
                    help="preview only: show what the current settings change compared "
                         "with REF - a git revision of the same file (default HEAD) or "
                         "another .toml file")
    args = ap.parse_args()

    if args.diff and args.command != "preview":
        ap.error("--diff only applies to `preview`")

    # The project directory, not the package directory: settings.toml, .secrets.toml
    # and service_account.json all live next to the repository root.
    base = Path(__file__).resolve().parent.parent
    cfg_path = Path(args.config) if Path(args.config).is_absolute() else base / args.config
    # The event tag comes from the file's `profile` key, so the TISS and TUWEL
    # settings own separate events and neither can delete the other's.
    settings = load_config(cfg_path)

    raw = parse_feed(fetch_feed(settings.ical_url))

    if args.command == "list":
        cmd_list(raw)  # unfiltered on purpose, so you see everything
        return

    if args.diff:
        other = _diff_settings(args.diff, cfg_path)
        print(f"{args.diff}  ->  {cfg_path.name} (working copy)\n")
        # The committed version is "before": these are the changes your edits introduce.
        print(compare(raw, other, settings).report())
        return

    events = build_events(raw, settings)

    if args.command == "preview":
        cmd_preview(events, settings)
    elif args.command == "export":
        cmd_export(events, Path(args.out))
    elif args.command == "sync":
        cmd_sync(events, settings, base)


if __name__ == "__main__":
    main()
