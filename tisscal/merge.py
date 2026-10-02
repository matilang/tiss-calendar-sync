"""Folding events that describe one slot held in several rooms into a single event."""
from __future__ import annotations

import hashlib
from collections import defaultdict

from .model import Lecture


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
