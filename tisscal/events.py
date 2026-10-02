"""Turning scraped course pages into calendar events.

Exams and registration deadlines exist nowhere in the iCal feed, so they are built here
from what tisscal.scrape reads off the course page.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Mapping
from datetime import datetime, timedelta

from .config import Scrape
from .model import VIENNA, Lecture
from .titles import course_label


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


def scraped_events(scrape: Scrape, titles: Mapping[str, str], scraper=None) -> list[Lecture]:
    """Exam dates and registration deadlines from the TISS course pages.

    The iCal feed has neither: it only lists lectures, and only for courses you are
    already registered for. Registration windows that have already opened are skipped
    for the "opens" reminder - there is nothing left to warn about - but their closing
    reminder is kept while the window is still open.
    """
    numbers = [str(c) for c in scrape.courses]
    if not numbers:
        return []

    if scraper is None:
        # Imported here, not at module level, so bs4 stays optional for anyone who
        # only syncs the iCal feed. Injectable so tests need no network.
        from .scrape import scrape as scraper

    semester = scrape.semester
    want_close = scrape.registration_close_reminder
    # Which registration reminders are worth having. Course and group sign-up happens
    # once at the start of term, so they are dead weight afterwards; exam registration
    # windows keep opening throughout the semester.
    kinds = set(scrape.registration_kinds)
    now = datetime.now(VIENNA)
    out: list[Lecture] = []

    for course in scraper(numbers, semester):
        nr = course.course_nr
        dotted = nr if "." in nr else f"{nr[:3]}.{nr[3:]}"
        # Use the configured short name so exams read "EXAM VU ASE Test 1" rather
        # than a bare number - stripping the number outright would leave "EXAM Test 1".
        pretty = course_label(dotted, titles) or dotted

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
