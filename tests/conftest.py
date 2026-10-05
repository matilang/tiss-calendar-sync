"""Shared test fixtures.

The iCal fixture is hand-written rather than a dump of a real feed: it holds exactly the
edge cases this project got wrong at some point, it contains nobody's real timetable, and
it does not drift when TISS changes. The course page is a saved real page, because
hand-writing window-scoped JSF HTML would prove nothing - that file is public TU Wien
content.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"

import tiss_sync as T  # noqa: E402  (needs the sys.path line above)


@pytest.fixture
def feed_bytes() -> bytes:
    return (FIXTURES / "feed.ics").read_bytes()


@pytest.fixture
def events(feed_bytes) -> list:
    return T.parse_feed(feed_bytes)


@pytest.fixture
def course_html() -> str:
    return (FIXTURES / "course_186814.html").read_text(encoding="utf-8")


@pytest.fixture
def cfg():
    """Settings covering the whole fixture semester, with the features switched on.

    Built from a dict through the same loader the real config goes through, so a change
    to validation or defaults shows up here instead of only in production.
    """
    from tisscal.config import from_dict

    return from_dict({
        "courses": [],
        "exclude_keywords": ["Sprechstunde"],
        "placeholder_min_hours": 4,
        "merge_parallel_rooms": True,
        "semester": {"start": "2026-10-01", "end": "2027-02-28"},
        "google": {"calendar_id": "test@group.calendar.google.com"},
        "tiss": {"ical_url": "https://example.invalid/feed.ics"},
        "titles": {"186.814": "VU Algorithmics", "194.187": "VU ASE",
                   "192.161": "VU MoGD"},
        "exercises": {"keywords": ["Exercise", "Übung"], "type_code": "UE",
                      "color_id": "7"},
        "retention": {"prune_past": False, "keep_past_kinds": ["exam"],
                      "keep_past_keywords": ["Exercise", "Q&A", "Q & A"]},
        "scrape": {"courses": []},
        "reminders": {
            "lecture_minutes_before": [15],
            "exam_minutes_before": [4320, 1440],
            "exam_keywords": ["Prüfung", "Exam", "Test"],
            "exam_color_id": "11",
            "registration_minutes_before": [1440, 0],
            "group_registration_minutes_before": [0],
            "registration_color_id": "5",
        },
    }, profile="test")


def make_event(course_nr="186.814", summary="186.814 VU Algorithmics",
               description="Lecture", *, start=None, hours=2.0, uid=None,
               kind="lecture", scope="", location="", all_day=False):
    """A Lecture built to order, for the rules that do not need a whole feed."""
    start = start or datetime(2026, 11, 10, 10, 0, tzinfo=T.VIENNA)
    if all_day:
        return T.Lecture(uid=uid or f"u-{description}", summary=summary,
                         start=start.date(), end=start.date() + timedelta(days=1),
                         location=location, description=description,
                         course_nr=course_nr, kind=kind, scope=scope)
    return T.Lecture(uid=uid or f"u-{description}-{hours}", summary=summary, start=start,
                     end=start + timedelta(hours=hours), location=location,
                     description=description, course_nr=course_nr, kind=kind, scope=scope)


def shift(settings, **changes):
    """Settings with some sections changed, without mutating the fixture.

    A section given as a dict is merged into the existing one, so a test can override a
    single option: shift(cfg, exercises={"type_code": "UE"}).
    """
    import dataclasses

    patch = {}
    for name, value in changes.items():
        current = getattr(settings, name)
        if isinstance(value, dict) and dataclasses.is_dataclass(current):
            value = {k: tuple(v) if isinstance(v, list) else v for k, v in value.items()}
            patch[name] = dataclasses.replace(current, **value)
        elif isinstance(value, list):
            patch[name] = tuple(value)
        else:
            patch[name] = value
    return dataclasses.replace(settings, **patch)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly on any real HTTP call.

    Not paranoia: the package refactor silently broke a monkeypatch and the scraper
    tests started querying TISS for real. They still passed - only the runtime gave it
    away, jumping from 0.7 s to 12 s. A test that reaches the network is not a test.
    """
    def forbidden(*args, **kwargs):
        raise AssertionError(
            "a test tried to make a real HTTP request - inject a fake instead")

    import requests

    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(requests, "get", forbidden)
