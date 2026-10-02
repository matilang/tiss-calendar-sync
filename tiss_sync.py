#!/usr/bin/env python3
"""
tiss_sync.py - Filter your TISS calendar feed down to the courses you actually
take this semester and push them into Google Calendar (with exam reminders).

Commands:
  python tiss_sync.py list      # show every course found in the feed -> pick yours
  python tiss_sync.py preview   # show what would be synced, nothing is written
  python tiss_sync.py export    # write a cleaned .ics file (import it manually)
  python tiss_sync.py sync      # create/update/delete events in Google Calendar
"""
from __future__ import annotations

import argparse
import hashlib
import random
import re
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar, Event

# Use the OS certificate store instead of certifi's bundle. Needed when a local
# antivirus / corporate proxy re-signs HTTPS traffic (its root is in the Windows
# store but not in certifi), which otherwise fails with CERTIFICATE_VERIFY_FAILED.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

VIENNA = ZoneInfo("Europe/Vienna")
COURSE_NR = re.compile(r"\b(\d{3}\.[0-9A-Z]{3})\b")  # e.g. 185.A91, 104.265
SOURCE_TAG = "tiss_sync"
# TISS stamps each UID with the time the feed was generated, e.g.
# "20261107T103712Z-5491884@tiss.tuwien.ac.at" - the same lecture comes back
# under a new UID on every request. Only the part after the timestamp identifies it.
UID_TIMESTAMP = re.compile(r"^\d{8}T\d{6}Z-")


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


# --------------------------------------------------------------------------- #
# Feed parsing
# --------------------------------------------------------------------------- #
@dataclass
class Lecture:
    uid: str
    summary: str
    start: datetime | date
    end: datetime | date
    location: str
    description: str
    course_nr: str | None
    # "lecture" from the iCal feed, or "exam"/"registration" built by tiss_scrape.
    # Decides which reminder set and colour the event gets.
    kind: str = "lecture"
    # iCal CATEGORIES. TUWEL puts the course and its semester here ("192.161-2026W")
    # while the title says only "Project Report ist fällig" - it is the only way to tell
    # which course a deadline belongs to, and to spot last year's leftovers.
    categories: str = ""
    # For kind == "registration": what you are registering for - "exam", "course" or
    # "group". Exam sign-up wants a warning days ahead; an exercise group is
    # first-come-first-served, so there the only useful moment is when it opens.
    scope: str = ""

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, datetime)

    @property
    def start_dt(self) -> datetime:
        if isinstance(self.start, datetime):
            return self.start
        return datetime.combine(self.start, time.min, VIENNA)

    @property
    def stable_uid(self) -> str:
        """UID without the feed-generation timestamp TISS prefixes onto it."""
        return UID_TIMESTAMP.sub("", self.uid)

    @property
    def gcal_id(self) -> str:
        # Google event ids allow [a-v0-9]; hex digest fits and is stable per event.
        # Hash the timestamp-stripped UID only: TISS issues one UID per occurrence, so the
        # start time adds no uniqueness, and leaving it out lets a rescheduled lecture be
        # updated in place instead of deleted and recreated.
        return hashlib.sha1(self.stable_uid.encode()).hexdigest()


def _to_aware(v):
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=VIENNA)
    return v


def fetch_feed(url_or_path: str) -> bytes:
    if url_or_path.startswith(("http://", "https://")):
        r = requests.get(url_or_path, timeout=30)
        r.raise_for_status()
        return r.content
    return Path(url_or_path).read_bytes()


def parse_feed(raw: bytes) -> list[Lecture]:
    cal = Calendar.from_ical(raw)
    out: list[Lecture] = []
    for comp in cal.walk("VEVENT"):
        summary = str(comp.get("SUMMARY", "")).strip()
        desc = str(comp.get("DESCRIPTION", "")).strip()
        start = _to_aware(comp.decoded("DTSTART"))
        end = _to_aware(comp.decoded("DTEND")) if comp.get("DTEND") else start
        cats = comp.get("CATEGORIES")
        if cats is not None:
            cats = ",".join(str(c) for c in getattr(cats, "cats", [])) or str(cats)
        cats = (cats or "").strip()
        m = COURSE_NR.search(summary) or COURSE_NR.search(desc) or COURSE_NR.search(cats)
        out.append(Lecture(
            uid=str(comp.get("UID", summary)),
            summary=summary,
            start=start,
            end=end,
            location=str(comp.get("LOCATION", "")).strip(),
            description=desc,
            course_nr=m.group(1) if m else None,
            categories=cats,
        ))
    return sorted(out, key=lambda e: e.start_dt)


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #
def matches_course(ev: Lecture, wanted: list[str]) -> bool:
    """A course entry can be a course number ('185.A91') or a piece of the title."""
    if not wanted:
        return True
    hay = f"{ev.summary} {ev.description} {ev.categories}".lower()
    for w in wanted:
        w_l = w.lower()
        if ev.course_nr and ev.course_nr.lower() == w_l:
            return True
        if w_l in hay:
            return True
    return False


def filter_events(events: list[Lecture], cfg: dict) -> list[Lecture]:
    sem = cfg.get("semester", {})
    start = date.fromisoformat(sem["start"]) if "start" in sem else date.today()
    end = date.fromisoformat(sem["end"]) if "end" in sem else date.today() + timedelta(days=200)
    lo = datetime.combine(start, time.min, VIENNA)
    hi = datetime.combine(end, time.max, VIENNA)
    excl = [k.lower() for k in cfg["exclude_keywords"]]

    kept = []
    for ev in events:
        if not (lo <= ev.start_dt <= hi):
            continue
        # categories included so a stale semester tag ("-2025W") can be excluded
        if any(k in f"{ev.summary} {ev.categories}".lower() for k in excl):
            continue
        if not matches_course(ev, cfg["courses"]):
            continue
        kept.append(ev)
    return kept


def drop_placeholders(events: list[Lecture], min_hours: float) -> list[Lecture]:
    """Drop exercise blocks that stand in for a group slot you have not picked yet.

    Until you register for an exercise group, TISS publishes the whole range the groups
    run in, once per room: 194.187 lists Friday 09:00-19:00 for both FAV EG A and FAV
    EG C, although your slot will be one hour in one room. Keeping them means two
    useless 10-hour blocks on every such Friday.

    Duration separates them cleanly - real lectures in this feed run 1-2 h and the
    placeholders run 10 h - so anything at or above `min_hours` is dropped. All-day
    events are exempt: those are the holiday markers, not placeholders.

    After you register, TISS emits your actual 1 h slot and the next sync picks it up
    with no change needed here.
    """
    if min_hours <= 0:
        return events
    kept = []
    for ev in events:
        if not ev.all_day and isinstance(ev.end, datetime):
            if (ev.end - ev.start).total_seconds() / 3600 >= min_hours:
                continue
        kept.append(ev)
    return kept


def prune_past(events: list[Lecture], cfg: dict) -> list[Lecture]:
    """Drop events that are over, except the ones worth keeping as a record.

    With a daily sync the calendar fills up with lectures you already sat through and
    reminders for deadlines that have passed. Exams and exercise sessions are different:
    looking back at "when exactly was that exercise" is useful, so they stay.

    Dropping an event here removes it from `wanted`, so the normal cleanup pass deletes
    it from the calendar - no separate deletion path.
    """
    ret = cfg.get("retention", {})
    if not ret.get("prune_past", False):
        return events

    keep_kinds = set(ret.get("keep_past_kinds", ["exam"]))
    keep_words = [w.lower() for w in ret.get("keep_past_keywords", [])]
    now = datetime.now(VIENNA)

    kept = []
    for ev in events:
        # An all-day event is only over once the whole day is.
        if isinstance(ev.end, datetime):
            finished = ev.end
        else:
            finished = datetime.combine(ev.end, time.max, VIENNA)
        if finished >= now:
            kept.append(ev)
            continue
        if ev.kind in keep_kinds:
            kept.append(ev)
            continue
        if any(w in f"{ev.summary} {ev.description}".lower() for w in keep_words):
            kept.append(ev)
            continue
    return kept


# TISS course-type codes that can lead a title. An exercise slot of a VU is still an
# Übung, so the code is swapped rather than the course renamed.
TYPE_CODES = ("VU", "VO", "UE", "PR", "SE", "LU", "PV", "AG", "EX")


def is_exercise(ev: Lecture, cfg: dict) -> bool:
    """Does this event look like an exercise slot rather than a lecture?

    Matched on the description, which is where TISS says what a slot is ("Lecture",
    "Exercise sessions", "Algorithmics Exercises 4"). Deliberately does not match the
    Q&A sessions - those are not exercises and should keep the lecture treatment.
    """
    words = cfg.get("exercises", {}).get("keywords", ["Exercise", "Übung", "Uebung"])
    hay = f"{ev.summary} {ev.description}".lower()
    return any(w.lower() in hay for w in words)


def hide_exercises(events: list[Lecture], cfg: dict) -> list[Lecture]:
    """Drop exercise slots for courses where you have not picked a group yet.

    Listed per course on purpose. TISS mixes two different things under "exercise":
    186.814's "Algorithmics Exercises 1-3" is a two-hour plenary session in a lecture
    hall, while the actual group slots are one hour in a seminar room and only reach the
    feed once you register. Which of those you want to see before registering is a
    judgement call per course, not something to guess from the data.

    Remove the course number from `hide_for` once you are in a group.
    """
    spec = cfg.get("exercises", {})
    hidden = {str(c) for c in spec.get("hide_for", [])}
    if not hidden:
        return events

    kept, dropped = [], []
    for ev in events:
        if ev.course_nr in hidden and ev.kind == "lecture" and is_exercise(ev, cfg):
            dropped.append(ev)
        else:
            kept.append(ev)

    # A plenary exercise session runs 2 h in a lecture hall; a personal group slot is
    # about an hour. Hiding something that short probably means registration came
    # through and this list is now costing you your own slot - say so loudly, because
    # silently missing it is exactly the failure this feature invites.
    for ev in dropped:
        if isinstance(ev.end, datetime) and (ev.end - ev.start) <= timedelta(minutes=90):
            print(f"  WARNING: hiding a short exercise slot for {ev.course_nr} "
                  f"({ev.start_dt:%a %d.%m %H:%M}, {(ev.end - ev.start).seconds // 60} min)."
                  f"\n           That looks like your own group slot - if you are "
                  f"registered now,\n           remove {ev.course_nr!r} from "
                  f"[exercises].hide_for to see it.", file=sys.stderr)
            break
    return kept


def course_label(course_nr: str | None, cfg: dict) -> str:
    """Short name configured for a course in [titles], or "" if none is set."""
    return cfg.get("titles", {}).get(course_nr or "", "")


def display_title(ev: Lecture, cfg: dict) -> str:
    """What the event is called on the calendar.

    Two different shapes come in. TISS titles already name the course and lead with its
    number ("186.814 VU Algorithmics"), which is just noise once you know your own
    courses - so the number comes off, and a [titles] entry can shorten the rest
    ("VU Management of Graph Data" -> "VU MoGD"). TUWEL titles instead say only what is
    due ("Project Report ist fällig") and never mention the course, so the short name
    goes in front; without it the deadline would be unattributable.
    """
    label = course_label(ev.course_nr, cfg)
    title = ev.summary

    if ev.course_nr and ev.course_nr in title:
        title = label or title.replace(ev.course_nr, "", 1).strip(" -–:")
    elif label and label in title:
        # Exam and registration events are built with the label already in them
        # ("EXAM VU ASE Test 1") - prefixing again would repeat it.
        pass
    elif ev.course_nr:
        title = f"{label or ev.course_nr} {title}".strip()

    return _exercise_code(title, ev, cfg)


def _exercise_code(title: str, ev: Lecture, cfg: dict) -> str:
    """Swap the leading course-type code on exercise slots: "VU Algorithmics" -> "UE ...".

    The course type is VU (lecture plus exercise), but an individual exercise slot is an
    Übung, and telling the two apart at a glance in the calendar is the whole point.
    Only applies to feed events - an exam or a registration reminder keeps the course's
    own code.
    """
    code = cfg.get("exercises", {}).get("type_code", "UE")
    if not code or ev.kind != "lecture" or not is_exercise(ev, cfg):
        return title
    head, _, rest = title.partition(" ")
    if head in TYPE_CODES and rest:
        return f"{code} {rest}"
    return title


def merge_parallel(events: list[Lecture]) -> list[Lecture]:
    """Collapse events that are the same slot held in several rooms into one event.

    TISS publishes one event per room. 194.187's exercise sessions, for example, are
    listed twice on each Friday 09:00-19:00 - once for Seminarraum FAV EG A and once
    for FAV EG C - which shows up on the calendar as a duplicate. Same course, same
    title, same start and end: one event, both rooms in the location.

    The merged event's id is derived from every UID that went into it, so it stays
    stable across runs, and it changes if TISS adds or drops a room.
    """
    buckets: dict[tuple, list[Lecture]] = defaultdict(list)
    for ev in events:
        buckets[(ev.course_nr, ev.summary, ev.start_dt, ev.end)].append(ev)

    out = []
    for group in buckets.values():
        if len(group) == 1:
            out.append(group[0])
            continue
        rooms = sorted({e.location for e in group if e.location})
        first = group[0]
        out.append(Lecture(
            # A merge key rather than a single UID: whichever room TISS lists first
            # must not change the id, so sort the parts.
            uid="+".join(sorted(e.stable_uid for e in group)),
            summary=first.summary,
            start=first.start,
            end=first.end,
            location=" / ".join(rooms),
            description=(first.description + f" ({len(group)} parallel rooms)").strip(),
            course_nr=first.course_nr,
            kind=first.kind,
        ))
    return sorted(out, key=lambda e: e.start_dt)


def is_exam(ev: Lecture, cfg: dict) -> bool:
    kws = cfg["reminders"].get("exam_keywords",
                               ["prüfung", "exam", "test", "klausur", "abgabe", "deadline"])
    text = f"{ev.summary} {ev.description}".lower()
    return any(k.lower() in text for k in kws)


# --------------------------------------------------------------------------- #
# Exams and registration deadlines (scraped, not in the feed)
# --------------------------------------------------------------------------- #
def _synthetic(kind: str, key: str, summary: str, start: datetime, end: datetime,
               location: str, description: str, course_nr: str | None,
               scope: str = "") -> Lecture:
    """Build an event for something scraped, which has no TISS UID of its own.

    `scope` is taken from the key's first segment when not given, so every registration
    event is automatically labelled exam / course / group without repeating it.
    """
    return Lecture(uid=f"{kind}|{key}", summary=summary, start=start, end=end,
                   location=location, description=description, course_nr=course_nr,
                   kind=kind, scope=scope or key.split("-")[0].split("|")[0])


def _at(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso).replace(tzinfo=VIENNA)
    except (ValueError, TypeError):
        return None


def scraped_events(cfg: dict) -> list[Lecture]:
    """Exam dates and registration deadlines from the TISS course pages.

    The iCal feed has neither: it only lists lectures, and only for courses you are
    already registered for. Registration windows that have already opened are skipped
    for the "opens" reminder - there is nothing left to warn about - but their closing
    reminder is kept while the window is still open.
    """
    spec = cfg.get("scrape", {})
    numbers = [str(c) for c in spec.get("courses", [])]
    if not numbers:
        return []

    import tiss_scrape   # local: keeps bs4 optional for feed-only use

    semester = spec.get("semester", "2026W")
    want_close = spec.get("registration_close_reminder", True)
    # Which registration reminders are worth having. Course and group sign-up happens
    # once at the start of term, so they are dead weight afterwards; exam registration
    # windows keep opening throughout the semester.
    kinds = set(spec.get("registration_kinds", ["exam", "course", "group"]))
    now = datetime.now(VIENNA)
    out: list[Lecture] = []

    for course in tiss_scrape.scrape(numbers, semester):
        nr = course.course_nr
        dotted = nr if "." in nr else f"{nr[:3]}.{nr[3:]}"
        # Use the configured short name so exams read "EXAM VU ASE Test 1" rather
        # than a bare number - stripping the number outright would leave "EXAM Test 1".
        pretty = course_label(dotted, cfg) or dotted

        # One registration window usually covers several sittings: 192.161's exams on
        # 08.01 and 11.01 share a single window, and 194.187 runs each test in up to five
        # rooms at once. Collect the windows so one reminder covers all of them instead of
        # one per room or per sitting.
        windows: dict[tuple[str, str, str], list[str]] = defaultdict(list)

        for ex in course.exams:
            begin = _at(f"{ex.date}T{ex.start or '00:00'}")
            finish = _at(f"{ex.date}T{ex.end or ex.start or '23:59'}")
            if not begin:
                continue
            window = (f"Registration {ex.apply_from.replace('T', ' ')} - "
                      f"{ex.apply_to.replace('T', ' ')} via {ex.apply_via}"
                      if ex.apply_from else "No registration window listed")
            # Room is deliberately left out of the key: parallel rooms are one exam, and
            # merge_parallel later folds them into a single event listing every room.
            out.append(_synthetic(
                "exam", f"{nr}|{ex.date}|{ex.start}|{ex.title}",
                f"EXAM {pretty} {ex.title}", begin, finish or begin,
                ex.room, f"{ex.mode}. {window}", dotted))
            if ex.apply_from:
                windows[(ex.apply_from, ex.apply_to, ex.apply_via)].append(
                    f"{ex.date} {ex.start} {ex.title}")

        for (w_from, w_to, via), sittings in (windows.items() if "exam" in kinds else ()):
            unique = sorted(set(sittings))
            listed = "; ".join(unique)
            opens, closes = _at(w_from), _at(w_to)
            if opens and opens > now:
                out.append(_synthetic(
                    "registration", f"exam-open|{nr}|{w_from}",
                    f"Register for {pretty} exam", opens, opens + timedelta(minutes=30),
                    via, f"Registration opens now, closes {w_to.replace('T', ' ')}. "
                         f"Sittings: {listed}", dotted))
            if want_close and closes and closes > now:
                out.append(_synthetic(
                    "registration", f"exam-close|{nr}|{w_to}",
                    f"LAST CHANCE to register for {pretty} exam",
                    closes - timedelta(minutes=30), closes, via,
                    f"Registration closes now. Sittings: {listed}", dotted))

        reg = course.registration if "course" in kinds else {}
        for label, iso, kind_key in (("opens", reg.get("begin"), "course-open"),
                                     ("closes", reg.get("end"), "course-close")):
            when = _at(iso or "")
            if not when or when <= now or (label == "closes" and not want_close):
                continue
            # An "opens" event starts at the moment it opens; a "closes" event has to end
            # at the deadline, or a 23:59 cut-off would show up as tomorrow's event.
            begin = when if label == "opens" else when - timedelta(minutes=30)
            out.append(_synthetic(
                "registration", f"{kind_key}|{nr}",
                f"Course registration {label}: {pretty}",
                begin, begin + timedelta(minutes=30), "TISS",
                f"TISS course registration for {pretty} {label} at this time.", dotted))

        # Every exercise group usually shares one window; one reminder covers them all.
        group_windows = course.group_registration if "group" in kinds else []
        for label, picker in (("opens", "from"), ("closes", "to")):
            if label == "closes" and not want_close:
                continue
            for moment in sorted({g.get(picker, "") for g in group_windows}):
                when = _at(moment)
                if not when or when <= now:
                    continue
                names = sorted({g["group"] for g in group_windows
                                if g.get(picker) == moment})
                begin = when if label == "opens" else when - timedelta(minutes=30)
                out.append(_synthetic(
                    "registration", f"group-{label}|{nr}|{moment}",
                    f"Exercise group registration {label}: {pretty}",
                    begin, begin + timedelta(minutes=30), "TISS",
                    f"{len(names)} groups: {', '.join(names[:12])}"
                    f"{' ...' if len(names) > 12 else ''}", dotted))

    return out


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


def _gcal_body(ev: Lecture, cfg: dict) -> dict:
    def ts(v):
        if isinstance(v, datetime):
            return {"dateTime": v.isoformat(), "timeZone": "Europe/Vienna"}
        return {"date": v.isoformat()}

    rem = cfg["reminders"]
    if ev.kind == "registration":
        # Exam sign-up is worth a warning days ahead. An exercise group is
        # first-come-first-served and opens at a published hour, so a day-early nudge is
        # useless there - the only moment that matters is the moment it opens.
        default = [24 * 60, 0]
        minutes = rem.get(f"{ev.scope}_registration_minutes_before",
                          rem.get("registration_minutes_before", default))
        color = rem.get("registration_color_id", "5")   # 5 = banana
    elif ev.kind == "exam" or is_exam(ev, cfg):
        minutes = rem.get("exam_minutes_before", [3 * 24 * 60, 24 * 60])
        color = rem.get("exam_color_id", "11")          # 11 = red
    else:
        minutes = rem.get("lecture_minutes_before", [15])
        color = None

    title = display_title(ev, cfg)

    body = {
        "summary": title,
        "location": ev.location,
        "description": ev.description,
        "start": ts(ev.start),
        "end": ts(ev.end),
        "reminders": {"useDefault": False,
                      "overrides": [{"method": "popup", "minutes": m} for m in minutes]},
        "extendedProperties": {"private": {"source": cfg["_tag"]}},
        "status": "confirmed",
    }
    if color:
        body["colorId"] = color
    return body


RETRY_STATUS = {403, 429, 500, 502, 503, 504}
THROTTLE_REASONS = ("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded")


def _execute(request, tries: int = 6):
    """Run a Google API request, backing off on throttling and transient server errors.

    Without this a single rate-limit response aborts the sync halfway through, leaving
    part of the semester on the calendar. 409 is passed straight through: cmd_sync uses
    it to tell "already exists" apart from a real failure.
    """
    from googleapiclient.errors import HttpError

    for attempt in range(tries):
        try:
            return request.execute()
        except HttpError as e:
            status = e.resp.status
            last = attempt == tries - 1
            if status == 409 or status not in RETRY_STATUS or last:
                raise
            if status == 403 and not any(r in str(e) for r in THROTTLE_REASONS):
                raise  # a genuine permission error, not throttling
            delay = 2 ** attempt + random.uniform(0, 1)
            print(f"  Google returned {status}; retrying in {delay:.1f}s",
                  file=sys.stderr)
            sleep(delay)


def cmd_sync(events: list[Lecture], cfg: dict, base: Path) -> None:
    from googleapiclient.errors import HttpError
    from gcal_auth import get_service

    svc = get_service(base)
    cal_id = cfg["google"]["calendar_id"]
    wanted = {ev.gcal_id: ev for ev in events}

    created = updated = revived = deleted = 0
    for gid, ev in wanted.items():
        body = _gcal_body(ev, cfg) | {"id": gid}
        try:
            _execute(svc.events().insert(calendarId=cal_id, body=body))
            created += 1
        except HttpError as e:
            if e.resp.status != 409:
                raise
            # 409 means the id is taken - but that includes ids Google is only holding as
            # a tombstone for an event deleted earlier. For those, update returns 200 and
            # leaves status "cancelled", so the event stays invisible while the sync
            # happily reports it as updated. Only an explicit patch brings it back.
            resp = _execute(svc.events().update(calendarId=cal_id, eventId=gid, body=body))
            if resp.get("status") == "cancelled":
                _execute(svc.events().patch(calendarId=cal_id, eventId=gid,
                                            body={"status": "confirmed"}))
                revived += 1
            else:
                updated += 1

    # Remove events this script created earlier that are no longer in the filtered feed
    # (e.g. you removed a course from config, or TISS cancelled a lecture).
    sem = cfg.get("semester", {})
    t_min = datetime.combine(date.fromisoformat(sem["start"]) if "start" in sem else date.today(),
                             time.min, VIENNA).isoformat()
    page = None
    while True:
        resp = _execute(svc.events().list(
            calendarId=cal_id, timeMin=t_min, pageToken=page,
            privateExtendedProperty=f"source={cfg['_tag']}",
            singleEvents=True, maxResults=2500))
        for item in resp.get("items", []):
            if item["id"] not in wanted:
                _execute(svc.events().delete(calendarId=cal_id, eventId=item["id"]))
                deleted += 1
        page = resp.get("nextPageToken")
        if not page:
            break

    report = f"Done: {created} created, {updated} updated, {deleted} removed"
    if revived:
        report += f", {revived} restored after an earlier deletion"
    print(report + ".")


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["list", "preview", "export", "sync"])
    ap.add_argument("-c", "--config", default="config.toml")
    ap.add_argument("-o", "--out", default="tiss_clean.ics", help="output file for `export`")
    args = ap.parse_args()

    base = Path(__file__).resolve().parent
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
