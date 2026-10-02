"""The transformation rules: filtering, placeholders, exercises, merging, pruning."""
from __future__ import annotations

from datetime import datetime, timedelta

import tiss_sync as T
from conftest import make_event, shift


class TestFilter:
    def test_keeps_only_the_semester_window(self, events, cfg):
        kept = T.filter_events(events, cfg)
        summaries = {e.summary for e in kept}
        # July 2026 and March 2027 both fall outside 2026-10-01 .. 2027-02-28
        assert not any("Database Systems" in s for s in summaries)
        assert not any("Knowledge Graphs" in s for s in summaries)

    def test_exclude_keyword_drops_an_event(self, events, cfg):
        assert not [e for e in T.filter_events(events, cfg)
                    if "Sprechstunde" in e.description]

    def test_exclude_keyword_also_matches_categories(self, events, cfg):
        """TUWEL keeps last year's course instances; the semester is only in CATEGORIES."""
        kept = T.filter_events(events, shift(cfg, exclude_keywords=["-2025W"]))
        assert not [e for e in kept if "2025W" in e.categories]
        assert [e for e in kept if e.categories == "192.161-2026W"]

    def test_course_filter_by_number(self, events, cfg):
        kept = T.filter_events(events, shift(cfg, courses=["186.814"]))
        assert kept and {e.course_nr for e in kept} == {"186.814"}

    def test_course_filter_matches_categories_too(self, events, cfg):
        """A TUWEL deadline has the number nowhere but in CATEGORIES."""
        kept = T.filter_events(events, shift(cfg, courses=["192.161"]))
        assert [e for e in kept if e.summary.startswith("Project Report")]

    def test_empty_filter_keeps_everything_in_window(self, events, cfg):
        kept = T.filter_events(events, shift(cfg, exclude_keywords=[]))
        assert len(kept) == len(events) - 2  # minus the two out-of-window events


class TestPlaceholders:
    """Before you pick an exercise group TISS publishes the whole span the groups run in,
    once per room - 09:00-19:00 twice on the same Friday."""

    def test_long_block_is_dropped(self, events, cfg):
        kept = T.drop_placeholders(events, 4)
        assert not [e for e in kept if e.description == "Exercise sessions"]

    def test_normal_lecture_survives(self, events, cfg):
        kept = T.drop_placeholders(events, 4)
        assert [e for e in kept if e.description == "Lecture"]

    def test_all_day_holiday_is_never_a_placeholder(self, events):
        kept = T.drop_placeholders(events, 4)
        assert [e for e in kept if "National Day" in e.summary]

    def test_threshold_zero_disables_the_rule(self, events):
        assert len(T.drop_placeholders(events, 0)) == len(events)

    def test_boundary_is_inclusive(self):
        """A block exactly at the threshold counts as a placeholder."""
        assert T.drop_placeholders([make_event(description="Exercise", hours=4)], 4) == []
        assert len(T.drop_placeholders([make_event(description="Exercise", hours=3.9)], 4)) == 1


class TestHideExercises:
    def test_nothing_hidden_when_list_is_empty(self, events, cfg):
        assert len(T.hide_exercises(events, cfg)) == len(events)

    def test_hides_only_the_listed_course(self, events, cfg):
        c = shift(cfg, exercises={"hide_for": ["186.814"]})
        kept = T.hide_exercises(events, c)
        assert not [e for e in kept
                    if e.course_nr == "186.814" and "Exercises" in e.description]
        assert [e for e in kept if e.course_nr == "194.187"]

    def test_qa_sessions_are_not_exercises(self, events, cfg):
        """Q&A is not an exercise and must keep the ordinary lecture treatment."""
        c = shift(cfg, exercises={"hide_for": ["186.814"]})
        kept = {e.description for e in T.hide_exercises(events, c)}
        assert "Algorithmics Q&A" in kept
        assert "Q & A 2" in kept

    def test_lectures_of_a_hidden_course_stay(self, events, cfg):
        c = shift(cfg, exercises={"hide_for": ["186.814"]})
        kept = T.hide_exercises(events, c)
        assert [e for e in kept if e.course_nr == "186.814" and e.description == "Lecture"]

    def test_warns_when_hiding_a_short_slot(self, cfg, capsys):
        """After registering, your own 1 h slot matches too - it must not vanish quietly."""
        c = shift(cfg, exercises={"hide_for": ["186.814"]})
        T.hide_exercises([make_event(description="Exercise Group 7", hours=1)], c)
        assert "WARNING" in capsys.readouterr().err

    def test_silent_for_a_plenary_block(self, cfg, capsys):
        c = shift(cfg, exercises={"hide_for": ["186.814"]})
        T.hide_exercises([make_event(description="Algorithmics Exercises 1-3", hours=2)], c)
        assert "WARNING" not in capsys.readouterr().err


class TestMergeParallel:
    def test_same_slot_in_two_rooms_becomes_one_event(self, events, cfg):
        gen_ai = [e for e in events if "Generative AI" in e.summary]
        assert len(gen_ai) == 2
        merged = T.merge_parallel(gen_ai)
        assert len(merged) == 1
        assert " / " in merged[0].location

    def test_merged_id_is_stable_whatever_the_order(self, events):
        gen_ai = [e for e in events if "Generative AI" in e.summary]
        assert (T.merge_parallel(gen_ai)[0].gcal_id
                == T.merge_parallel(list(reversed(gen_ai)))[0].gcal_id)

    def test_merged_id_changes_when_a_room_is_added(self, events):
        gen_ai = [e for e in events if "Generative AI" in e.summary]
        extra = make_event(course_nr=gen_ai[0].course_nr, summary=gen_ai[0].summary,
                           description=gen_ai[0].description, start=gen_ai[0].start,
                           uid="third-room", location="Another room")
        assert T.merge_parallel(gen_ai)[0].gcal_id != T.merge_parallel(gen_ai + [extra])[0].gcal_id

    def test_a_lone_event_is_untouched(self, events):
        one = [e for e in events if e.description == "Lecture" and "Algorithmics" in e.summary][:1]
        assert T.merge_parallel(one)[0].uid == one[0].uid


class TestPrunePast:
    def _cfg(self, cfg):
        return shift(cfg, retention={"prune_past": True})

    def _past(self, **kw):
        start = datetime.now(T.VIENNA) - timedelta(days=7)
        return make_event(start=start, **kw)

    def test_disabled_by_default(self, cfg):
        past = [self._past(description="Lecture")]
        assert len(T.prune_past(past, cfg)) == 1

    def test_past_lecture_is_dropped(self, cfg):
        assert T.prune_past([self._past(description="Lecture")], self._cfg(cfg)) == []

    def test_past_exam_is_kept(self, cfg):
        kept = T.prune_past([self._past(description="written", kind="exam")], self._cfg(cfg))
        assert len(kept) == 1

    def test_past_exercise_is_kept(self, cfg):
        kept = T.prune_past([self._past(description="Algorithmics Exercises 4")],
                            self._cfg(cfg))
        assert len(kept) == 1

    def test_both_spellings_of_qa_are_kept(self, cfg):
        """TISS writes it as "Algorithmics Q&A" and as "Q & A 2"."""
        for desc in ("Algorithmics Q&A", "Q & A 2"):
            assert len(T.prune_past([self._past(description=desc)], self._cfg(cfg))) == 1

    def test_past_registration_reminder_is_dropped(self, cfg):
        ev = self._past(description="opens", kind="registration", scope="exam")
        assert T.prune_past([ev], self._cfg(cfg)) == []

    def test_future_events_are_untouched(self, cfg):
        future = make_event(start=datetime.now(T.VIENNA) + timedelta(days=7))
        assert len(T.prune_past([future], self._cfg(cfg))) == 1

    def test_an_event_still_running_is_kept(self, cfg):
        now = datetime.now(T.VIENNA)
        ongoing = make_event(start=now - timedelta(hours=1), hours=3)
        assert len(T.prune_past([ongoing], self._cfg(cfg))) == 1

    def test_all_day_event_survives_its_own_day(self, cfg):
        today = make_event(start=datetime.now(T.VIENNA), all_day=True,
                           summary="National Day, no lectures", description="")
        assert len(T.prune_past([today], self._cfg(cfg))) == 1
