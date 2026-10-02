"""What happens to events between the feed and the calendar.

Pure functions: events in, events out. No network, no Google, no clock except where a
rule is explicitly about "now" - which is what makes this the part worth testing.
"""
from __future__ import annotations

import hashlib
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from .model import TYPE_CODES, UID_TIMESTAMP, VIENNA, Lecture


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
