"""Calendar titles, and the event body handed to Google."""
from __future__ import annotations

from datetime import datetime, timedelta

import tiss_sync as T
from conftest import make_event, shift


class TestTitles:
    def test_course_number_is_dropped(self, cfg):
        ev = make_event(summary="186.814 VU Algorithmics")
        assert T.display_title(ev, cfg) == "VU Algorithmics"

    def test_long_name_is_shortened_by_the_label(self, cfg):
        ev = make_event(course_nr="194.187",
                        summary="194.187 VU Advanced Software Engineering")
        assert T.display_title(ev, cfg) == "VU ASE"

    def test_course_without_a_label_just_loses_the_number(self, cfg):
        ev = make_event(course_nr="192.039", summary="192.039 VU Deep Learning")
        assert T.display_title(ev, cfg) == "VU Deep Learning"

    def test_event_with_no_course_is_left_alone(self, cfg):
        ev = make_event(course_nr=None, summary="National Day, no lectures")
        assert T.display_title(ev, cfg) == "National Day, no lectures"

    def test_tuwel_deadline_gets_the_label_in_front(self, cfg):
        """TUWEL titles never name their course, so it has to be prefixed."""
        ev = make_event(course_nr="192.161", summary="Project Report ist fällig.",
                        description="")
        assert T.display_title(ev, cfg) == "VU MoGD Project Report ist fällig."

    def test_label_is_not_repeated_on_a_synthetic_event(self, cfg):
        """Exam and registration summaries are built with the label already inside."""
        ev = make_event(course_nr="194.187", summary="EXAM VU ASE Test 1",
                        description="written", kind="exam")
        assert T.display_title(ev, cfg) == "EXAM VU ASE Test 1"

    def test_exercise_slot_is_relabelled_ue(self, cfg):
        """An exercise of a VU is still an Übung."""
        ev = make_event(summary="186.814 VU Algorithmics",
                        description="Algorithmics Exercises 4")
        assert T.display_title(ev, cfg) == "UE Algorithmics"

    def test_lecture_keeps_vu(self, cfg):
        ev = make_event(summary="186.814 VU Algorithmics", description="Lecture")
        assert T.display_title(ev, cfg) == "VU Algorithmics"

    def test_qa_keeps_vu(self, cfg):
        ev = make_event(summary="186.814 VU Algorithmics", description="Algorithmics Q&A")
        assert T.display_title(ev, cfg) == "VU Algorithmics"

    def test_exam_keeps_the_course_code(self, cfg):
        """Only feed events are relabelled; an exam is not an Übung."""
        ev = make_event(summary="EXAM VU Algorithmics Prüfung", description="written",
                        kind="exam")
        assert T.display_title(ev, cfg).startswith("EXAM VU")

    def test_relabelling_can_be_switched_off(self, cfg):
        c = shift(cfg, exercises={"type_code": ""})
        ev = make_event(summary="186.814 VU Algorithmics", description="Exercises 4")
        assert T.display_title(ev, c) == "VU Algorithmics"


class TestGcalBody:
    def test_lecture_reminders(self, cfg):
        body = T._gcal_body(make_event(), cfg)
        assert [o["minutes"] for o in body["reminders"]["overrides"]] == [15]
        assert "colorId" not in body

    def test_exam_reminders_and_colour(self, cfg):
        body = T._gcal_body(make_event(kind="exam", description="written"), cfg)
        assert sorted(o["minutes"] for o in body["reminders"]["overrides"]) == [1440, 4320]
        assert body["colorId"] == "11"

    def test_exam_detected_by_keyword_alone(self, cfg):
        """A TUWEL deadline arrives as a plain feed event; keywords are all we have."""
        body = T._gcal_body(make_event(description="Test 1"), cfg)
        assert sorted(o["minutes"] for o in body["reminders"]["overrides"]) == [1440, 4320]

    def test_exam_registration_warns_a_day_ahead(self, cfg):
        ev = make_event(kind="registration", scope="exam", description="")
        body = T._gcal_body(ev, cfg)
        assert sorted(o["minutes"] for o in body["reminders"]["overrides"]) == [0, 1440]

    def test_group_registration_fires_only_when_it_opens(self, cfg):
        """Groups are first-come-first-served, so a day-early nudge is useless."""
        ev = make_event(kind="registration", scope="group", description="")
        body = T._gcal_body(ev, cfg)
        assert [o["minutes"] for o in body["reminders"]["overrides"]] == [0]

    def test_unknown_scope_falls_back_to_the_general_setting(self, cfg):
        ev = make_event(kind="registration", scope="course", description="")
        body = T._gcal_body(ev, cfg)
        assert sorted(o["minutes"] for o in body["reminders"]["overrides"]) == [0, 1440]

    def test_body_is_tagged_so_cleanup_only_touches_our_events(self, cfg):
        body = T._gcal_body(make_event(), cfg)
        assert body["extendedProperties"]["private"]["source"] == "tiss_sync-test"

    def test_status_is_confirmed(self, cfg):
        """Set explicitly, because updating a tombstoned id otherwise stays cancelled."""
        assert T._gcal_body(make_event(), cfg)["status"] == "confirmed"

    def test_timed_event_carries_the_timezone(self, cfg):
        body = T._gcal_body(make_event(), cfg)
        assert body["start"]["timeZone"] == "Europe/Vienna"
        assert "dateTime" in body["start"]

    def test_all_day_event_uses_a_plain_date(self, cfg):
        body = T._gcal_body(make_event(all_day=True, description=""), cfg)
        assert "date" in body["start"] and "dateTime" not in body["start"]
