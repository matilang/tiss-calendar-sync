"""Exercise groups: reading whatever TISS calls them, and keeping only your own.

Two things are being protected here.

The first is that the matcher stays loose enough to survive a renaming. The naming is not
a standard - 194.187 uses "3_11:00-12:00", 192.216 uses "Exercises Group B", and the next
course could use "Gruppe 2" - so the point of groups.py is that none of those spellings is
privileged. A test per spelling is cheap and is the only thing that will notice when a
regex tweak quietly drops one.

The second is that it cannot eat a lecture. A wrong letter in [groups] should cost you your
exercise slots, which is visible and fixable in a click, and nothing else. Every rule that
keeps that true has a test, including the ones that look obvious.
"""
from __future__ import annotations

from datetime import datetime

import pytest

import tiss_sync as T
from tisscal import groups
from tisscal.filters import keep_chosen_groups
from conftest import make_event, shift

EOAI = "192.216"


# --------------------------------------------------------------------------- #
# Reading the group out of a description
# --------------------------------------------------------------------------- #
class TestGroupOf:
    @pytest.mark.parametrize("text, token", [
        # The two shapes in the live feed today.
        ("Exercises Group A (Labs)", "A"),
        ("Exercises Group B", "B"),
        ("Exercises Group C", "C"),
        ("Exercises Group C [TEMPORARY]", "C"),
        ("194.187 Advanced Software Engineering 3_11:00-12:00", "3"),
        ("1_09:00-10:00", "1"),
        ("16_18:00-19:00", "16"),
        # Spellings TISS could switch to without telling anyone. The whole reason this
        # module exists rather than one regex at the call site.
        ("Gruppe 1", "1"),
        ("Übungsgruppe 3", "3"),
        ("Ubungsgruppe 3", "3"),
        ("Uebungsgruppe 12", "12"),
        ("Gr. B", "B"),
        ("Gr B", "B"),
        ("GRP 4", "4"),
        ("group: A1", "A1"),
        ("Exercise Group 2b", "2B"),
        ("Kohorte 3", "3"),
        ("exercise group b", "B"),
    ])
    def test_it_reads_the_group(self, text, token):
        assert groups.group_of(text) == token

    @pytest.mark.parametrize("text", [
        "", "Lecture", "Introduction Lecture", "Algorithmics Q&A", "Exercise sessions",
        "Algorithmics Exercises 4", "Vorbesprechung",
        # The ones a careless pattern gets wrong. "Group of" and "Graph" both sit one
        # character away from matching, and a false positive here does not look like a bug:
        # it looks like a course whose lectures vanished.
        "Group of students", "Working Group Meeting", "Management of Graph Data",
        "Grades", "Great lecture", "Grund der Veranstaltung",
    ])
    def test_it_finds_no_group(self, text):
        assert groups.group_of(text) == ""

    def test_a_leading_zero_is_the_same_group(self):
        """TISS writes both. Treating them as two groups would filter out the one you
        picked and leave the calendar empty for that course."""
        assert groups.group_of("Gruppe 02") == groups.group_of("Gruppe 2") == "2"

    def test_the_description_is_all_that_is_read(self):
        """Not the title: the title is the course name, which every event carries. A group
        named there would tag the lectures as belonging to a group too."""
        ev = make_event(summary="192.216 VU The Essentials of AI Group B",
                        description="Lecture")
        assert groups.group_of(ev.description) == ""


class TestWanted:
    @pytest.mark.parametrize("written", [
        "B", "b", " b ", "Group B", "group b", "Gruppe B", "Gr. B", "Exercises Group B",
    ])
    def test_however_the_setting_is_written(self, written):
        """The value in the file goes through the same reader as the feed, so you can
        paste what TISS showed you instead of working out what to type."""
        assert groups.wanted(written) == "B"

    @pytest.mark.parametrize("written", ["3", "03", "Gruppe 3", "3_11:00-12:00"])
    def test_and_for_a_numbered_group(self, written):
        assert groups.wanted(written) == "3"

    def test_nothing_means_nothing(self):
        assert groups.wanted("") == ""


class TestFoundIn:
    def test_it_reports_each_group_once_with_its_full_name(self):
        """The interface turns this into the list you choose from, and "A" on its own is
        not something anyone recognises."""
        found = groups.found_in(["Lecture", "Exercises Group A (Labs)", "Exercises Group B",
                                 "Exercises Group A (Labs)", "Exercises Group B"])
        assert found == {"A": "Exercises Group A (Labs)", "B": "Exercises Group B"}

    def test_a_feed_with_no_groups_yields_none(self):
        assert groups.found_in(["Lecture", "Q&A", ""]) == {}


# --------------------------------------------------------------------------- #
# Dropping the groups that are not yours
# --------------------------------------------------------------------------- #
@pytest.fixture
def eoai():
    """One course, three groups, plus a lecture - the 192.216 shape in miniature."""
    def slot(description, hour):
        return make_event(course_nr=EOAI, summary=f"{EOAI} VU The Essentials of AI",
                          description=description, hours=2,
                          start=datetime(2026, 11, 10, hour, 0, tzinfo=T.VIENNA))
    return [slot("Lecture", 10), slot("Exercises Group A (Labs)", 13),
            slot("Exercises Group B", 15), slot("Exercises Group C", 17)]


class TestKeepChosenGroups:
    def test_it_keeps_the_chosen_group_and_the_lecture(self, eoai):
        kept = keep_chosen_groups(eoai, {EOAI: "B"})
        assert [e.description for e in kept] == ["Lecture", "Exercises Group B"]

    def test_no_choice_keeps_everything(self, eoai):
        """The default has to be harmless: a course nobody has configured must look exactly
        as it did before this module existed."""
        assert keep_chosen_groups(eoai, {}) == eoai

    def test_an_event_naming_no_group_is_never_dropped(self, eoai):
        """The property that makes a wrong letter survivable. Whatever is chosen, and
        however little it matches, the lecture stays."""
        for choice in ("A", "B", "C", "Z", "nonsense"):
            kept = keep_chosen_groups(eoai, {EOAI: choice})
            assert any(e.description == "Lecture" for e in kept), choice

    def test_a_group_that_does_not_exist_removes_every_group_slot(self, eoai):
        """Not silently nothing: picking a group the feed has none of is a visible mistake,
        and the interface shows the count before it is saved."""
        kept = keep_chosen_groups(eoai, {EOAI: "Z"})
        assert [e.description for e in kept] == ["Lecture"]

    def test_other_courses_are_untouched(self, eoai):
        other = make_event(course_nr="194.187", description="Exercises Group A")
        kept = keep_chosen_groups(eoai + [other], {EOAI: "B"})
        assert other in kept

    def test_the_setting_may_be_written_as_tiss_shows_it(self, eoai):
        assert (keep_chosen_groups(eoai, {EOAI: "Exercises Group B"})
                == keep_chosen_groups(eoai, {EOAI: "B"}))

    def test_a_scraped_event_listing_every_group_survives(self, eoai):
        """The one that would otherwise disappear. The group-registration reminder names
        all the groups in its description - "3 groups: Exercises Group A (Labs), ..." - so
        a filter that read it as an event of group A would delete the reminder for anyone
        in group B. Guarded by kind, so it holds wherever in the pipeline this runs."""
        reminder = make_event(
            course_nr=EOAI, summary="Exercise group registration opens: VU EoAI",
            description="3 groups: Exercises Group A (Labs), Exercises Group B, "
                        "Exercises Group C [TEMPORARY]",
            kind="registration", scope="group")
        assert reminder in keep_chosen_groups(eoai + [reminder], {EOAI: "B"})


# --------------------------------------------------------------------------- #
# Through the whole pipeline, which is where it has to hold
# --------------------------------------------------------------------------- #
class TestInThePipeline:
    def test_choosing_a_group_removes_the_others(self, eoai, cfg):
        settings = shift(cfg, courses=[EOAI], scrape={"courses": []})
        before = T.build_events(eoai, settings, scraper=lambda n, s: [])
        after = T.build_events(eoai, shift(settings, groups={EOAI: "B"}),
                               scraper=lambda n, s: [])
        assert len(before) == 4 and len(after) == 2
        assert {e.description for e in after} == {"Lecture", "Exercises Group B"}

    def test_the_kept_slot_still_reads_as_an_exercise(self, eoai, cfg):
        """Filtering must not cost the exercise its colour and its UE code - the two
        things that tell it apart from the lecture beside it."""
        from tisscal.classify import is_exercise
        from tisscal.titles import display_title

        settings = shift(cfg, courses=[EOAI], scrape={"courses": []},
                         groups={EOAI: "B"}, titles={EOAI: "VU EoAI"})
        kept = T.build_events(eoai, settings, scraper=lambda n, s: [])
        slot = [e for e in kept if "Group B" in e.description][0]
        assert is_exercise(slot, settings.exercises)
        assert display_title(slot, settings.titles, settings.exercises) == "UE EoAI"
