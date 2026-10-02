"""Which events belong on the calendar at all."""
from __future__ import annotations

import sys
from datetime import datetime, time, timedelta

from .classify import is_exercise
from .config import Exercises, Retention, Semester
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


def hide_exercises(events: list[Lecture], exercises: Exercises) -> list[Lecture]:
    """Drop exercise slots for courses where you have not picked a group yet.

    Listed per course on purpose. TISS mixes two different things under "exercise":
    186.814's "Algorithmics Exercises 1-3" is a two-hour plenary session in a lecture
    hall, while the actual group slots are one hour in a seminar room and only reach the
    feed once you register. Which of those you want to see before registering is a
    judgement call per course, not something to guess from the data.

    Remove the course number from `hide_for` once you are in a group.
    """
    hidden = {str(c) for c in exercises.hide_for}
    if not hidden:
        return events

    kept, dropped = [], []
    for ev in events:
        if ev.course_nr in hidden and ev.kind == "lecture" and is_exercise(ev, exercises):
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


def prune_past(events: list[Lecture], retention: Retention) -> list[Lecture]:
    """Drop events that are over, except the ones worth keeping as a record.

    With a daily sync the calendar fills up with lectures you already sat through and
    reminders for deadlines that have passed. Exams and exercise sessions are different:
    looking back at "when exactly was that exercise" is useful, so they stay.

    Dropping an event here removes it from `wanted`, so the normal cleanup pass deletes
    it from the calendar - no separate deletion path.
    """
    if not retention.prune_past:
        return events

    keep_kinds = set(retention.keep_past_kinds)
    keep_words = [w.lower() for w in retention.keep_past_keywords]
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
