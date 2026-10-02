"""tisscal - TISS and TUWEL schedules into Google Calendar.

Where things live:

    model.py     the Lecture event type and the constants that define identity
    config.py    reading config.toml and defaulting every option
    feed.py      fetching and parsing an iCal feed
    pipeline.py  the rules: filtering, placeholders, exercises, merging, titles, pruning
    scrape.py    reading exam dates and registration windows off a TISS course page
    events.py    turning scraped course data into calendar events
    gcal.py      the Google Calendar side: event bodies, retries, the sync itself
    cli.py       list / preview / export / sync

The rules in pipeline.py are pure functions - events in, events out - which is why they
are the part covered by tests. Nothing there touches the network or Google.
"""
from .config import load_config
from .feed import fetch_feed, parse_feed
from .model import SOURCE_TAG, VIENNA, Lecture

__all__ = ["Lecture", "VIENNA", "SOURCE_TAG", "load_config", "fetch_feed", "parse_feed"]
