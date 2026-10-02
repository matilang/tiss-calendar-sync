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

Two positions are conventional rather than required, and saying otherwise would be
untrue: `exercises` could run after scraping, because hide_exercises only touches
kind == "lecture" and so can never reach a registration reminder; and `prune` could run
before scraping, because events.py already refuses to emit a window that has closed.

tools/check_ordering.py reorders the steps one at a time and reports which moves the
tests actually catch - worth re-running after adding a step, since a comment claiming an
order matters is worth nothing on its own.
"""
from __future__ import annotations

from .events import scraped_events
from .filters import drop_placeholders, filter_events, hide_exercises, prune_past
from .merge import merge_parallel
from .model import Lecture


def build_events(raw: list[Lecture], cfg: dict, scraper=None) -> list[Lecture]:
    """Everything the calendar should contain, from the raw feed and the course pages.

    `scraper` is injectable so this can be exercised without touching the network.
    """
    events = filter_events(raw, cfg)
    events = drop_placeholders(events, cfg["placeholder_min_hours"])
    events = hide_exercises(events, cfg)

    if cfg["scrape"].get("courses"):
        events += scraped_events(cfg, scraper=scraper)

    if cfg["merge_parallel_rooms"]:
        events = merge_parallel(events)

    events = prune_past(events, cfg)
    return sorted({e.gcal_id: e for e in events}.values(), key=lambda e: e.start_dt)
