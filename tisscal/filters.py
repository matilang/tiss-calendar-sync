"""Which events belong on the calendar at all."""
from __future__ import annotations

import re
from datetime import datetime, time
from typing import Mapping

from . import groups as groups_mod
from .config import Retention, Semester
from .model import VIENNA, Lecture


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #
def matches_course(ev: Lecture, wanted: tuple[str, ...]) -> bool:
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


def filter_events(events: list[Lecture], semester: Semester,
                  courses: tuple[str, ...], exclude_keywords: tuple[str, ...],
                  ) -> list[Lecture]:
    """Keep what is inside the semester window, wanted, and not excluded."""
    lo = datetime.combine(semester.start, time.min, VIENNA)
    hi = datetime.combine(semester.end, time.max, VIENNA)
    excl = [k.lower() for k in exclude_keywords]

    kept = []
    for ev in events:
        if not (lo <= ev.start_dt <= hi):
            continue
        # categories included so a stale semester tag ("-2025W") can be excluded
        if any(k in f"{ev.summary} {ev.categories}".lower() for k in excl):
            continue
        if not matches_course(ev, courses):
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


def keep_chosen_groups(events: list[Lecture], chosen: Mapping[str, str]) -> list[Lecture]:
    """Drop the exercise slots of groups that are not yours.

    Needed because some courses put every group's appointments in the feed rather than only
    the one you registered for - 192.216 delivers all three, so 28 of its 42 exercise slots
    belong to other people. Which courses do that, and why being told is the only way to
    know, is in groups.py.

    Only events that name a group are candidates. An event with no group marker is a
    lecture, a Q&A or a plenary session, and those belong to everyone - so switching this
    on can never remove a lecture, which is the property that makes a wrong letter in the
    settings file an inconvenience rather than a disaster.

    Scraped events are not reached here: the one that lists every group's name in its
    description ("3 groups: Exercises Group A (Labs), ...") would otherwise read as another
    group's event and the registration reminder would vanish. Hence the kind check, which
    holds wherever in the pipeline this runs rather than relying on it running early.
    """
    if not chosen:
        return events
    wanted = {nr: groups_mod.wanted(value) for nr, value in chosen.items()}

    kept = []
    for ev in events:
        mine = wanted.get(ev.course_nr or "")
        if not mine or ev.kind != "lecture":
            kept.append(ev)
            continue
        group = groups_mod.group_of(ev.description or "")
        if not group or group == mine:
            kept.append(ev)
    return kept


def prune_past(events: list[Lecture], retention: Retention) -> list[Lecture]:
    """Drop events that are over, except the ones worth keeping as a record.

    With a daily sync the calendar fills up with lectures you already sat through and
    reminders for deadlines that have passed. Exams and exercise sessions are different:
    looking back at "when exactly was that exercise" is useful, so they stay.

    Dropping an event here removes it from `wanted`, so the normal cleanup pass deletes
    it from the calendar - no separate deletion path.

    The keywords match on a word boundary, not as bare substrings. As substrings they kept
    far more than they named: "Exercise" matched every "Exercises Group B" in the feed, so
    each course with named groups left its past exercise slots on the calendar for good,
    and "Test" would match "Latest". A keyword here means a word.
    """
    if not retention.prune_past:
        return events

    keep_kinds = set(retention.keep_past_kinds)
    # Leading boundary only, so "Test" still matches "Tests" - plural, not coincidence.
    keep_words = [re.compile(r"\b" + re.escape(w), re.IGNORECASE)
                  for w in retention.keep_past_keywords]
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
        if any(w.search(f"{ev.summary} {ev.description}") for w in keep_words):
            kept.append(ev)
            continue
    return kept
