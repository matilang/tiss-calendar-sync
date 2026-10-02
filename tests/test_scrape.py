"""Reading a TISS course page, and turning it into exam/registration events.

Runs against a saved copy of the page, so no network and no drift when TISS edits it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import tiss_scrape as ts
import tiss_sync as T
from conftest import shift


@pytest.fixture
def course(course_html):
    return ts.parse_course(course_html, "186814", "2026W")


class TestParseCoursePage:
    def test_finds_the_exams(self, course):
        assert len(course.exams) == 3

    def test_exam_carries_date_room_and_mode(self, course):
        first = course.exams[0]
        assert first.date == "2026-11-27"
        assert first.start == "14:00" and first.end == "16:00"
        assert "GM 1" in first.room
        assert first.mode == "written"

    def test_exam_carries_the_registration_window(self, course):
        """The thing the iCal feed cannot tell you: when you may actually sign up."""
        first = course.exams[0]
        assert first.apply_from == "2026-11-02T00:00"
        assert first.apply_to == "2026-11-25T23:59"
        assert first.apply_via == "TISS"

    def test_finds_the_course_registration_window(self, course):
        assert course.registration["begin"] == "2026-09-01T00:00"
        assert course.registration["end"] == "2026-10-11T23:59"

    def test_finds_every_exercise_group_with_its_hours(self, course):
        """Group hours are published before you register, so you can choose on them."""
        assert len(course.group_registration) == 11
        groups = [o for o in course.occurrences if o.group]
        assert groups
        one_hour = [o for o in groups if o.start and o.end and o.start != o.end]
        assert one_hour

    def test_group_registration_window(self, course):
        first = course.group_registration[0]
        assert first["from"] == "2026-10-08T09:00"
        assert first["to"] == "2026-10-14T18:00"

    def test_reports_the_paginated_table_as_incomplete(self, course):
        """TISS serves one page of the appointments table and the rest cannot be fetched,
        so this must be visible rather than silently truncated. The numbers come from the
        frozen fixture - re-save it and they move."""
        assert course.partial
        shown, total = next(iter(course.partial.values()))
        assert shown == 20
        assert total == 28
        assert not course.complete

    def test_a_shell_page_is_rejected_loudly(self):
        """A failed dsrid handshake returns a short "Loading..." stub. Parsing that would
        yield an empty but entirely plausible-looking course, so it has to raise."""
        class ShellResponse:
            text = "<html><body>Loading...</body></html>"
            encoding = "utf-8"

            def raise_for_status(self):
                pass

        class ShellSession:
            def get(self, *a, **kw):
                return ShellResponse()

        with pytest.raises(RuntimeError, match="handshake"):
            ts.fetch_course("186814", "2026W", ShellSession())


class TestScrapedEvents:
    """scraped_events() normally fetches; here the fetch is replaced so the mapping from
    course data to calendar events can be tested on its own."""

    @pytest.fixture
    def cfg_scrape(self, cfg):
        return shift(cfg, scrape={"courses": ["186814"], "semester": "2026W",
                                  "registration_kinds": ["exam", "course", "group"],
                                  "registration_close_reminder": True})

    @pytest.fixture(autouse=True)
    def _no_network(self, monkeypatch, course):
        monkeypatch.setattr(ts, "scrape", lambda numbers, semester: [course])

    def test_builds_one_event_per_exam(self, cfg_scrape):
        exams = [e for e in T.scraped_events(cfg_scrape) if e.kind == "exam"]
        assert len(exams) == 3
        assert all(e.course_nr == "186.814" for e in exams)

    def test_exam_title_uses_the_short_label(self, cfg_scrape):
        exams = [e for e in T.scraped_events(cfg_scrape) if e.kind == "exam"]
        assert all(e.summary.startswith("EXAM VU Algorithmics") for e in exams)

    def test_exam_room_is_kept_for_merging(self, cfg_scrape):
        exams = [e for e in T.scraped_events(cfg_scrape) if e.kind == "exam"]
        assert any("GM 1" in e.location for e in exams)

    def test_registration_events_carry_their_scope(self, cfg_scrape):
        regs = [e for e in T.scraped_events(cfg_scrape) if e.kind == "registration"]
        assert regs
        assert {e.scope for e in regs} <= {"exam", "course", "group"}

    def test_one_reminder_per_window_not_per_sitting(self, cfg_scrape):
        """Several sittings can share one registration window; reminding twice is noise."""
        opens = [e for e in T.scraped_events(cfg_scrape)
                 if e.kind == "registration" and "Register for" in e.summary]
        assert len(opens) == len({e.uid for e in opens})

    def test_group_scope_is_set(self, cfg_scrape):
        groups = [e for e in T.scraped_events(cfg_scrape)
                  if e.kind == "registration" and e.scope == "group"]
        assert groups

    def test_registration_kinds_can_switch_scopes_off(self, cfg_scrape):
        only_exam = shift(cfg_scrape, scrape={"registration_kinds": ["exam"]})
        scopes = {e.scope for e in T.scraped_events(only_exam) if e.kind == "registration"}
        assert scopes == {"exam"}

    def test_windows_already_open_get_no_opens_reminder(self, cfg_scrape):
        """Course registration began 2026-09-01; there is nothing left to warn about."""
        opens = [e for e in T.scraped_events(cfg_scrape)
                 if e.scope == "course" and "opens" in e.summary]
        assert opens == []

    def test_every_event_is_in_the_future(self, cfg_scrape):
        now = datetime.now(T.VIENNA)
        regs = [e for e in T.scraped_events(cfg_scrape) if e.kind == "registration"]
        assert all(e.start_dt > now - timedelta(days=1) for e in regs)

    def test_scraping_is_skipped_without_courses(self, cfg):
        assert T.scraped_events(cfg) == []
