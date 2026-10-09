"""The order the rules apply in.

This is the one place to read if you want to know what happens to an event between the
feed and the calendar. Each step lives in its own module - filters.py, merge.py,
events.py - and the sequence is here.

Three of these positions are load-bearing, and tests/test_build.py fails if they move:

    filter before scrape  - a retake exam is outside the lecture window, so scraping
                            after the semester filter is what keeps it
    placeholders before   - 186.814's retake runs four hours, exactly the placeholder
    scrape                  threshold, and would be thrown away as a group block
    merge after scrape    - an exam held in five rooms at once arrives as five rows;
                            merged late they become one event listing every room, and
                            unmerged the shared id silently drops four of them

One position is conventional rather than required, and saying otherwise would be untrue:
`prune` could run before scraping, because events.py already refuses to emit a
registration window that has closed.

There used to be a fourth step here, hiding the exercise slots of courses you had no group
in. It was removed: `drop_placeholders` already deals with the real case, because before
registration TISS publishes the whole ten-hour span the groups run in, and dropping
anything that long needs no per-course list to maintain.

`keep_chosen_groups` is not that step coming back. The placeholder rule handles the course
that publishes one long block per group; this handles the course that publishes every
group's real appointments to everybody - 192.216 sends all three groups' slots, 1-2 h
each, so there is no duration to tell them apart by and nothing in the feed says which is
yours. See groups.py.

tools/check_ordering.py reorders the steps one at a time and reports which moves the
tests actually catch - worth re-running after adding a step, since a comment claiming an
order matters is worth nothing on its own.
"""
from __future__ import annotations

from .config import Settings
from .events import scraped_events
from .filters import (drop_placeholders, filter_events, keep_chosen_groups,
                      prune_past)
from .merge import merge_parallel
from .model import Lecture


def build_events(raw: list[Lecture], settings: Settings, scraper=None) -> list[Lecture]:
    """Everything the calendar should contain, from the raw feed and the course pages.

    `scraper` is injectable so this can be exercised without touching the network.
    """
    events = filter_events(raw, settings.semester, settings.courses,
                           settings.exclude_keywords)
    events = drop_placeholders(events, settings.placeholder_min_hours)
    # Before scraping only for readability - it belongs with the other two feed filters.
    # keep_chosen_groups ignores anything that is not a feed event itself, so moving it
    # is safe and check_ordering.py is expected to report this move as uncaught.
    events = keep_chosen_groups(events, settings.groups)

    if settings.scrape.courses:
        events += scraped_events(settings.scrape, settings.titles, scraper=scraper)

    if settings.merge_parallel_rooms:
        events = merge_parallel(events)

    events = prune_past(events, settings.retention)
    return sorted({e.gcal_id: e for e in events}.values(), key=lambda e: e.start_dt)
