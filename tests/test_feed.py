"""Parsing the iCal feed, and the event identity everything else depends on."""
from __future__ import annotations

from datetime import datetime

import tiss_sync as T


def test_parses_every_event(events):
    assert len(events) == 14


def test_course_number_comes_from_the_summary(events):
    alg = [e for e in events if e.description == "Lecture" and "Algorithmics" in e.summary]
    assert alg and all(e.course_nr == "186.814" for e in alg)


def test_course_number_falls_back_to_categories(events):
    """TUWEL titles never name the course; CATEGORIES is the only place it appears."""
    tuwel = [e for e in events if e.summary.startswith("Project Report")]
    assert len(tuwel) == 1
    assert tuwel[0].course_nr == "192.161"
    assert tuwel[0].categories == "192.161-2026W"


def test_times_are_vienna_aware(events):
    timed = [e for e in events if isinstance(e.start, datetime)]
    assert timed and all(e.start.tzinfo is not None for e in timed)


def test_all_day_event_stays_a_date(events):
    holiday = [e for e in events if "National Day" in e.summary][0]
    assert holiday.all_day
    assert not isinstance(holiday.start, datetime)


class TestEventIdentity:
    """TISS stamps the feed-generation time into every UID, so the same lecture arrives
    under a different UID on every request. Hashing the raw UID made each sync delete and
    recreate the entire semester."""

    def test_timestamp_prefix_is_stripped(self):
        ev = T.Lecture(uid="20261107T103712Z-5491884@tiss.tuwien.ac.at", summary="x",
                       start=datetime(2026, 10, 5, tzinfo=T.VIENNA),
                       end=datetime(2026, 10, 5, tzinfo=T.VIENNA),
                       location="", description="", course_nr=None)
        assert ev.stable_uid == "5491884@tiss.tuwien.ac.at"

    def test_same_event_from_two_fetches_keeps_one_id(self):
        def at(stamp):
            return T.Lecture(uid=f"{stamp}-5491884@tiss.tuwien.ac.at", summary="x",
                             start=datetime(2026, 10, 5, tzinfo=T.VIENNA),
                             end=datetime(2026, 10, 5, tzinfo=T.VIENNA),
                             location="", description="", course_nr=None)
        assert at("20261107T103712Z").gcal_id == at("20261108T015950Z").gcal_id

    def test_different_events_get_different_ids(self, events):
        assert len({e.gcal_id for e in events}) == len(events)

    def test_id_is_a_legal_google_event_id(self, events):
        for ev in events:
            assert set(ev.gcal_id) <= set("abcdefghijklmnopqrstuv0123456789")
            assert len(ev.gcal_id) >= 5

    def test_id_ignores_the_start_time(self):
        """Keyed on the UID alone, so a rescheduled lecture is updated, not replaced."""
        def at(hour):
            return T.Lecture(uid="20261107T103712Z-5491884@tiss.tuwien.ac.at", summary="x",
                             start=datetime(2026, 10, 5, hour, tzinfo=T.VIENNA),
                             end=datetime(2026, 10, 5, hour + 2, tzinfo=T.VIENNA),
                             location="", description="", course_nr=None)
        assert at(10).gcal_id == at(14).gcal_id
