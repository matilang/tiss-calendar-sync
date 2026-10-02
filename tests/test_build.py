"""The pipeline as a whole: that the steps run in an order where each one is correct.

The individual rules are covered in test_pipeline.py. What is checked here is the
sequencing, because several of these steps are only right where they sit - and an ordering
mistake would not fail any single-rule test.
"""
from __future__ import annotations

import tisscal.scrape as sc
from tisscal.pipeline import build_events
from conftest import make_event, shift


def build(events, cfg, course=None):
    return build_events(events, cfg,
                        scraper=(lambda numbers, semester: [course]) if course else None)


def test_produces_events_with_unique_ids(events, cfg):
    out = build(events, cfg)
    assert out
    assert len({e.gcal_id for e in out}) == len(out)


def test_output_is_sorted_by_start(events, cfg):
    out = build(events, cfg)
    assert [e.start_dt for e in out] == sorted(e.start_dt for e in out)


def test_out_of_window_events_are_gone(events, cfg):
    titles = {e.summary for e in build(events, cfg)}
    assert not any("Database Systems" in t for t in titles)


def test_placeholders_and_excluded_events_are_gone(events, cfg):
    out = build(events, cfg)
    assert not [e for e in out if e.description == "Exercise sessions"]
    assert not [e for e in out if "Sprechstunde" in e.summary]


def test_parallel_rooms_arrive_merged(events, cfg):
    gen_ai = [e for e in build(events, cfg) if "Generative AI" in e.summary]
    assert len(gen_ai) == 1
    assert " / " in gen_ai[0].location


def test_merging_can_be_switched_off(events, cfg):
    out = build(events, shift(cfg, merge_parallel_rooms=False))
    assert len([e for e in out if "Generative AI" in e.summary]) == 2


def test_registration_reminders_survive_exercise_hiding(events, cfg, course_html):
    """While you wait for a group, the reminder to register is the one thing you want -
    and "Exercise group registration opens" matches the exercise keywords.

    What protects it is hide_exercises only touching kind == "lecture", not the step
    order: tools/check_ordering.py confirms moving this step past the scrape changes
    nothing. It lives outside TestOrdering for that reason.
    """
    course = sc.parse_course(course_html, "186814", "2026W")
    c = shift(cfg,
              exercises={"hide_for": ["186.814"]},
              scrape={"courses": ["186814"], "semester": "2026W",
                      "registration_kinds": ["group"],
                      "registration_close_reminder": True})
    out = build(events, c, course)
    assert not [e for e in out if e.kind == "lecture" and "Exercises" in e.description]
    assert [e for e in out if e.kind == "registration" and e.scope == "group"]


def test_no_closed_registration_window_reaches_the_calendar(events, cfg, course_html):
    """A window that has already closed is useless. events.py refuses to emit one, so
    this holds regardless of where prune_past sits - again verified by reordering."""
    course = sc.parse_course(course_html, "186814", "2026W")
    c = shift(cfg,
              retention={"prune_past": True},
              scrape={"courses": ["186814"], "semester": "2026W",
                      "registration_kinds": ["exam", "course", "group"]})
    out = build(events, c, course)
    assert [e for e in out if e.kind == "registration"]
    stale = [e for e in out if e.kind == "registration"
             and e.start_dt.year == 2026 and e.start_dt.month < 9]
    assert stale == []


class TestOrdering:
    """Each of these fails if the step moves - verified with tools/check_ordering.py.

    Only load-bearing positions belong here. A test that passes whatever the order is
    does not document an ordering requirement, it just looks like it does.
    """

    def test_scraped_exams_survive_the_semester_window(self, events, cfg,
                                                                  course_html):
        course = sc.parse_course(course_html, "186814", "2026W")
        """Exams are added after filter_events on purpose: 186.814's retake is in March,
        outside the lecture window, and is exactly what you want reminding about."""
        c = shift(cfg, scrape={"courses": ["186814"], "semester": "2026W",
                               "registration_kinds": []})
        march = [e for e in build(events, c, course)
                 if e.kind == "exam" and e.start_dt.month == 3]
        assert march, "the March retake was filtered out by the semester window"

    def test_a_long_scraped_exam_is_not_taken_for_a_placeholder(self, events, cfg,
                                                               course_html):
        """drop_placeholders must run before scraping. 186.814's retake runs 16:00-20:00,
        four hours - exactly the placeholder threshold."""
        course = sc.parse_course(course_html, "186814", "2026W")
        c = shift(cfg, scrape={"courses": ["186814"], "semester": "2026W",
                               "registration_kinds": []})
        exams = [e for e in build(events, c, course) if e.kind == "exam"]
        assert len(exams) == 3
        assert [e for e in exams
                if (e.end - e.start).total_seconds() / 3600 >= 4]



class TestParallelRoomExams:
    """194.187 runs each test in up to five rooms at once, which is why merging has to
    happen after the scraped events are added. The saved 186.814 page has no such exam,
    so the course here is built by hand."""

    @staticmethod
    def _course():
        from tisscal.scrape import Course, Exam
        shared = dict(title="Test 1", date="2026-11-17", start="18:00", end="20:00",
                      mode="written", apply_from="", apply_to="", apply_via="TISS")
        return Course(course_nr="194187", semester="2026W",
                      exams=[Exam(room="EI 7 Hoersaal", **shared),
                             Exam(room="FH Hoersaal 1", **shared),
                             Exam(room="GM 1 Audi. Max.", **shared)])

    def _cfg(self, cfg):
        return shift(cfg, scrape={"courses": ["194187"], "semester": "2026W",
                                  "registration_kinds": []})

    def test_one_exam_event_listing_every_room(self, events, cfg):
        out = build(events, self._cfg(cfg), self._course())
        exams = [e for e in out if e.kind == "exam"]
        assert len(exams) == 1
        for room in ("EI 7", "FH Hoersaal 1", "GM 1"):
            assert room in exams[0].location

    def test_without_merging_the_other_rooms_are_lost(self, events, cfg):
        """Not just a cosmetic difference: the parallel rows share one id, so with merging
        off the dedupe keeps a single row and the other rooms vanish silently."""
        out = build(events, shift(self._cfg(cfg), merge_parallel_rooms=False),
                    self._course())
        exams = [e for e in out if e.kind == "exam"]
        assert len(exams) == 1
        assert " / " not in exams[0].location
