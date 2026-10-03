"""tisscal - TISS and TUWEL schedules into Google Calendar.

Where things live:

    model.py     the Lecture event type and the constants that define identity
    config.py    reading settings.toml and defaulting every option
    secrets.py   where the iCal URL and calendar id come from, separately
    feed.py      fetching and parsing an iCal feed
    classify.py  is this an exercise? an exam?
    filters.py   which events belong on the calendar at all
    merge.py     folding one slot held in several rooms into a single event
    titles.py    what an event is called
    scrape.py    reading exam dates and registration windows off a TISS course page
    events.py    turning scraped course data into calendar events
    pipeline.py  the order all of the above applies in - start here
    plan.py      what a settings change would do, worked out before anything is sent
    settings_io.py  writing settings.toml back without losing its comments
    gcal.py      the Google Calendar side: event bodies, retries, the sync itself
    cli.py       list / preview / export / sync

filters, merge, titles and classify are pure functions - events in, events out - which is
why they are the part covered by tests. Nothing there touches the network or Google.
Read pipeline.py first: it is the order, and most steps are only correct where they sit.
"""
from .config import SOURCE_TAG, ConfigError, Settings, load_config
from .feed import fetch_feed, parse_feed
from .model import VIENNA, Lecture

__all__ = ["ConfigError", "Lecture", "SOURCE_TAG", "Settings", "VIENNA", "fetch_feed",
           "load_config", "parse_feed"]
