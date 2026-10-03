"""What a settings change would do to the calendar, worked out before anything is sent.

The sync has always been able to tell you what it *would* put on the calendar - that is
`preview`. What it could not tell you is the more useful thing: what **changes**. Hiding
one course's exercises, or switching a reminder off, moves a handful of events among a
hundred, and reading two lists of a hundred lines side by side is not a way to find them.

So this runs the pipeline twice, once per set of settings, and compares the Google request
bodies. No network beyond the feed, and no calls to Google at all: the bodies are what the
sync would send, so comparing them answers the question exactly.

Note what this is not. "What does my edit change?" and "what will the next sync do to the
calendar?" are different questions - the second needs reading the calendar, because it
depends on what is already there and on what the last run managed to write. This module
answers the first.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .config import Settings
from .gcal import _gcal_body
from .model import Lecture
from .pipeline import build_events


def event_bodies(events: list[Lecture], settings: Settings) -> dict[str, dict]:
    """The Google request body for every event, keyed by its calendar id."""
    return {ev.gcal_id: _gcal_body(ev, settings) for ev in events}


def once(scraper=None):
    """A scraper that fetches each request only once, however often it is called.

    Needed because comparing two settings runs the whole pipeline twice, and the pipeline
    scrapes TISS course pages. Without this, a diff over five courses would mean ten HTTP
    requests to TISS for data that cannot have changed in between.

    Cached per (courses, semester) request rather than per course, because that is the
    shape of the scraper's own interface. Two settings that scrape overlapping but
    different course lists will therefore fetch the overlap twice, which is rare enough
    not to be worth the complication.
    """
    cache: dict[tuple, list] = {}

    def wrapped(numbers, semester):
        key = (tuple(numbers), semester)
        if key not in cache:
            real = scraper
            if real is None:
                from .scrape import scrape as real
            cache[key] = real(numbers, semester)
        return cache[key]

    return wrapped


# --------------------------------------------------------------------------- #
# The difference
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Change:
    """One event that would appear, disappear, or come out different."""
    gcal_id: str
    kind: str                      # "created" | "updated" | "deleted"
    before: dict | None
    after: dict | None

    @property
    def body(self) -> dict:
        """Whichever side exists - for a deletion that is the old one."""
        return self.after if self.after is not None else (self.before or {})

    @property
    def title(self) -> str:
        return self.body.get("summary", "")

    @property
    def location(self) -> str:
        return self.body.get("location", "")

    @property
    def start(self) -> str:
        """ISO start, whether the event is timed or all-day."""
        start = self.body.get("start", {})
        return start.get("dateTime") or start.get("date") or ""

    @property
    def fields(self) -> tuple[str, ...]:
        """Which top-level body fields differ. Empty unless this is an update.

        Top-level is deliberate: "reminders" is more use than six lines of nested dict,
        and the full bodies are still on the object for anything that wants detail.
        """
        if self.before is None or self.after is None:
            return ()
        keys = set(self.before) | set(self.after)
        return tuple(sorted(k for k in keys
                            if self.before.get(k) != self.after.get(k)))


MARKS = {"created": "+", "updated": "~", "deleted": "-"}


@dataclass(frozen=True)
class Plan:
    changes: tuple[Change, ...]
    unchanged: int

    def of(self, kind: str) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.kind == kind)

    @property
    def created(self) -> tuple[Change, ...]:
        return self.of("created")

    @property
    def updated(self) -> tuple[Change, ...]:
        return self.of("updated")

    @property
    def deleted(self) -> tuple[Change, ...]:
        return self.of("deleted")

    def __bool__(self) -> bool:
        return bool(self.changes)

    @property
    def summary(self) -> str:
        return (f"+{len(self.created)} new, -{len(self.deleted)} gone, "
                f"~{len(self.updated)} changed, {self.unchanged} untouched")

    def report(self, limit: int | None = None) -> str:
        """The changes as lines, chronologically, most useful first on the left."""
        if not self.changes:
            return f"No change. {self.unchanged} events either way."

        lines = []
        for c in self.changes[:limit]:
            when = c.start[:16].replace("T", " ") or "(no date)"
            detail = f"  [{', '.join(c.fields)}]" if c.fields else ""
            lines.append(f"  {MARKS[c.kind]} {when}  {c.title[:46]:<46}"
                         f"{c.location[:20]:<20}{detail}")
        if limit is not None and len(self.changes) > limit:
            lines.append(f"  ... {len(self.changes) - limit} more")
        return "\n".join(lines) + f"\n\n{self.summary}."


def diff(before: Mapping[str, dict], after: Mapping[str, dict]) -> Plan:
    """Compare two {calendar id: request body} maps.

    Identity is the calendar id, which comes from the event's UID with the feed's
    generation timestamp stripped - so a lecture that moved room or time is one update,
    not a deletion plus a creation. That property is the whole reason the sync stopped
    rebuilding the semester on every run, and it is what makes this diff readable.
    """
    changes: list[Change] = []
    unchanged = 0

    for gid in set(before) | set(after):
        old, new = before.get(gid), after.get(gid)
        if old is None:
            changes.append(Change(gid, "created", None, new))
        elif new is None:
            changes.append(Change(gid, "deleted", old, None))
        elif old != new:
            changes.append(Change(gid, "updated", old, new))
        else:
            unchanged += 1

    # Chronological, then by title, so the same diff always reads the same way.
    changes.sort(key=lambda c: (c.start, c.title, c.gcal_id))
    return Plan(tuple(changes), unchanged)


def compare(raw: list[Lecture], before: Settings, after: Settings,
            scraper=None) -> Plan:
    """What changes on the calendar if `before` is replaced by `after`.

    The same raw feed is used for both sides on purpose: this is about the settings, and
    a feed that changed underneath would show up as noise nobody asked about.
    """
    shared = once(scraper)
    old = event_bodies(build_events(raw, before, scraper=shared), before)
    new = event_bodies(build_events(raw, after, scraper=shared), after)
    return diff(old, new)
