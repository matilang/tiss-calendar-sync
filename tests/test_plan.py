"""Working out what a settings change does to the calendar, before anything is sent.

The interesting cases here are the ones where a naive diff would read badly: an event that
moved must be one update rather than a deletion plus a creation, and running the pipeline
twice must not mean scraping TISS twice.
"""
from __future__ import annotations

import tisscal.scrape as sc
from tisscal.plan import Change, compare, diff, event_bodies, once
from tisscal.pipeline import build_events
from conftest import shift

BODY = {"summary": "VU Algorithmics", "start": {"dateTime": "2026-11-10T10:00:00+01:00"},
        "location": "Hoersaal 6"}


def other(**changes) -> dict:
    return BODY | changes


class TestTheDiffPrimitive:
    """Just two {id: body} maps - no pipeline, no settings, no feed."""

    def test_an_id_only_on_the_right_is_created(self):
        plan = diff({}, {"a": BODY})
        assert len(plan.created) == 1
        assert plan.created[0].gcal_id == "a"
        assert not plan.updated and not plan.deleted

    def test_an_id_only_on_the_left_is_deleted(self):
        plan = diff({"a": BODY}, {})
        assert len(plan.deleted) == 1
        assert plan.deleted[0].before == BODY
        assert plan.deleted[0].after is None

    def test_a_different_body_under_the_same_id_is_an_update(self):
        plan = diff({"a": BODY}, {"a": other(location="EI 8")})
        assert len(plan.updated) == 1
        assert plan.updated[0].fields == ("location",)

    def test_an_identical_body_is_counted_not_listed(self):
        plan = diff({"a": BODY}, {"a": dict(BODY)})
        assert not plan.changes
        assert plan.unchanged == 1
        assert not plan

    def test_every_differing_field_is_named(self):
        plan = diff({"a": BODY},
                    {"a": other(summary="UE Algorithmics", location="FAV 01 B")})
        assert plan.updated[0].fields == ("location", "summary")

    def test_a_creation_names_no_fields(self):
        """There is nothing to compare against, and claiming every field changed would
        make the report lie about what happened."""
        assert diff({}, {"a": BODY}).created[0].fields == ()
        assert diff({"a": BODY}, {}).deleted[0].fields == ()

    def test_changes_are_chronological(self):
        late = other(start={"dateTime": "2026-12-01T10:00:00+01:00"})
        early = other(start={"dateTime": "2026-10-01T10:00:00+02:00"})
        plan = diff({}, {"b": late, "a": early})
        assert [c.start[:10] for c in plan.changes] == ["2026-10-01", "2026-12-01"]

    def test_an_all_day_event_sorts_by_its_date(self):
        """All-day events carry `date`, not `dateTime`, and used to sort as empty."""
        plan = diff({}, {"a": other(start={"date": "2026-10-26"})})
        assert plan.changes[0].start == "2026-10-26"

    def test_the_summary_counts_each_kind(self):
        plan = diff({"gone": BODY, "same": BODY, "edit": BODY},
                    {"same": BODY, "edit": other(location="X"), "new": BODY})
        assert "+1 new" in plan.summary
        assert "-1 gone" in plan.summary
        assert "~1 changed" in plan.summary
        assert "1 untouched" in plan.summary


class TestAgainstTheRealPipeline:
    def test_the_same_settings_change_nothing(self, events, cfg):
        plan = compare(events, cfg, cfg)
        assert not plan
        assert plan.unchanged == len(build_events(events, cfg))

    def test_dropping_a_course_only_deletes(self, events, cfg):
        both = shift(cfg, courses=["186.814", "194.207"])
        one = shift(cfg, courses=["186.814"])
        plan = compare(events, both, one)
        assert plan.deleted and not plan.created and not plan.updated
        assert all("Generative AI" in c.before["summary"] for c in plan.deleted)

    def test_adding_a_course_only_creates(self, events, cfg):
        """194.207, not 194.187: the fixture carries 194.187 only as the long
        pre-registration placeholder blocks, which the pipeline drops, so adding that
        course would change nothing and prove nothing."""
        plan = compare(events, shift(cfg, courses=["186.814"]),
                       shift(cfg, courses=["186.814", "194.207"]))
        assert plan.created and not plan.deleted

    def test_renaming_a_course_updates_rather_than_replaces(self, events, cfg):
        """The calendar id comes from the feed UID, not the title, so a renamed course
        keeps its events. If this ever became create+delete, every rename would churn the
        whole calendar - which is the bug the stable id was introduced to fix.
        """
        before = shift(cfg, titles={"186.814": "VU Algorithmics"})
        after = shift(cfg, titles={"186.814": "VU Algo"})
        plan = compare(events, before, after)
        assert plan.updated
        assert not plan.created and not plan.deleted
        assert all(c.fields == ("summary",) for c in plan.updated)

    def test_turning_off_the_placeholder_rule_creates_events(self, events, cfg):
        """The pre-registration blocks come back - the one case where loosening a setting
        adds events instead of removing them.

        The two ten-hour blocks arrive as one event, because they are the same slot in two
        rooms and the merge step folds them together. They also come back as "UE ..." in
        the exercise colour, which is the rest of the pipeline agreeing that a block called
        "Exercise sessions" is an exercise.
        """
        plan = compare(events, cfg, shift(cfg, placeholder_min_hours=0))
        assert len(plan.created) == 1
        assert not plan.deleted and not plan.updated
        restored = plan.created[0].after
        assert "Exercise sessions" in restored["description"]
        assert "2 parallel rooms" in restored["description"]
        assert restored["summary"].startswith("UE ")
        assert restored["colorId"] == cfg.exercises.color_id

    def test_changing_reminders_touches_only_that_field(self, events, cfg):
        plan = compare(events, cfg,
                       shift(cfg, reminders={"lecture_minutes_before": [30, 5]}))
        assert plan.updated
        assert all(c.fields == ("reminders",) for c in plan.updated)

    def test_report_says_so_when_nothing_changed(self, events, cfg):
        assert "No change" in compare(events, cfg, cfg).report()

    def test_report_lists_each_change_once(self, events, cfg):
        plan = compare(events, cfg, shift(cfg, courses=["186.814"]))
        marked = [l for l in plan.report().splitlines() if l.startswith("  -")]
        assert len(marked) == len(plan.deleted)

    def test_report_can_be_truncated(self, events, cfg):
        plan = compare(events, cfg, shift(cfg, courses=["186.814"]))
        assert "more" in plan.report(limit=1)


class TestScrapingOnlyOnce:
    """Comparing settings runs the pipeline twice. The scraper must not run twice with it:
    five courses would mean ten fetches from TISS for data that cannot have changed in
    between, and TISS is a university server being asked a favour."""

    def scraped(self, course_html):
        calls = []

        def scraper(numbers, semester):
            calls.append((tuple(numbers), semester))
            return [sc.parse_course(course_html, "186814", "2026W")]

        return scraper, calls

    def test_two_pipeline_runs_scrape_once(self, events, cfg, course_html):
        scraper, calls = self.scraped(course_html)
        s = shift(cfg, scrape={"courses": ["186814"]})
        compare(events, s, s, scraper=scraper)
        assert len(calls) == 1, f"scraped {len(calls)} times"

    def test_the_cache_keys_on_the_request(self, events, cfg, course_html):
        """Different course lists are different questions and must both be asked."""
        scraper, calls = self.scraped(course_html)
        compare(events,
                shift(cfg, scrape={"courses": ["186814"]}),
                shift(cfg, scrape={"courses": ["186814", "194187"]}),
                scraper=scraper)
        assert len(calls) == 2
        assert calls[0] != calls[1]

    def test_once_passes_arguments_through_unchanged(self, course_html):
        scraper, calls = self.scraped(course_html)
        wrapped = once(scraper)
        wrapped(["186814"], "2026W")
        wrapped(["186814"], "2026W")
        assert calls == [(("186814",), "2026W")]

    def test_a_scraped_event_reaches_the_diff(self, events, cfg, course_html):
        """Without this, the three tests above could pass while scraping contributed
        nothing at all to the comparison."""
        scraper, _ = self.scraped(course_html)
        plan = compare(events, cfg, shift(cfg, scrape={"courses": ["186814"]}),
                       scraper=scraper)
        assert any(c.body.get("summary", "").startswith("EXAM") for c in plan.created)


def test_event_bodies_are_keyed_by_calendar_id(events, cfg):
    built = build_events(events, cfg)
    bodies = event_bodies(built, cfg)
    assert set(bodies) == {e.gcal_id for e in built}
    assert all("summary" in b for b in bodies.values())


def test_a_change_falls_back_to_the_old_body_for_display(events, cfg):
    """A deletion has no new body, and the report still has to name the event."""
    c = Change("x", "deleted", BODY, None)
    assert c.title == "VU Algorithmics"
    assert c.location == "Hoersaal 6"
