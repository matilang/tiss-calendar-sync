"""Loading the config: defaults in one place, and a typo that is an error."""
from __future__ import annotations

from datetime import date

import pytest

from tisscal.config import (ConfigError, Exercises, Reminders, Retention, Scrape,
                            Settings, from_dict)


class TestDefaults:
    def test_an_empty_config_is_usable(self):
        """Nothing is required, so a half-written config still runs rather than raising
        an AttributeError somewhere in the middle of a sync."""
        s = from_dict({})
        assert s.placeholder_min_hours == 4
        assert s.merge_parallel_rooms is True
        assert s.retention.prune_past is False
        assert s.scrape.courses == ()

    def test_defaults_live_only_here(self):
        """These used to be duplicated between the loader and the call sites - is_exam
        carried its own copy of exam_keywords - so a default could disagree with itself."""
        assert "Prüfung" in Reminders().exam_keywords
        assert Exercises().type_code == "UE"
        assert Retention().keep_past_kinds == ("exam",)
        assert "Q & A" in Retention().keep_past_keywords

    def test_lists_become_tuples(self):
        """Settings is frozen; a mutable default shared between instances is a trap."""
        s = from_dict({"courses": ["186.814", "194.187"]})
        assert s.courses == ("186.814", "194.187")

    def test_semester_dates_are_parsed(self):
        s = from_dict({"semester": {"start": "2026-10-01", "end": "2027-02-28"}})
        assert s.semester.start == date(2026, 10, 1)
        assert s.semester.end == date(2027, 2, 28)

    def test_section_values_reach_their_dataclass(self):
        s = from_dict({"tiss": {"ical_url": "https://example.invalid/f.ics"},
                       "google": {"calendar_id": "x@group.calendar.google.com"},
                       "exercises": {"hide_for": ["186.814"]}})
        assert s.ical_url == "https://example.invalid/f.ics"
        assert s.calendar_id == "x@group.calendar.google.com"
        assert s.exercises.hide_for == ("186.814",)

    def test_the_tag_comes_from_the_file_name(self):
        """config.toml and tuwel.toml must own separate events on one calendar."""
        assert from_dict({}, tag="tiss_sync-tuwel").tag == "tiss_sync-tuwel"


class TestRejectsTypos:
    """A misspelled key used to do nothing at all: the real option kept its default and
    nothing said so. These are the cases that actually happened or nearly did."""

    def test_unknown_top_level_option(self):
        with pytest.raises(ConfigError, match="placeholder_min_hour"):
            from_dict({"placeholder_min_hour": 4})

    def test_the_message_suggests_the_right_name(self):
        with pytest.raises(ConfigError, match="did you mean 'placeholder_min_hours'"):
            from_dict({"placeholder_min_hour": 4})

    def test_unknown_option_inside_a_section(self):
        with pytest.raises(ConfigError, match=r"\[exercises\].*hide_four"):
            from_dict({"exercises": {"hide_four": []}})

    def test_unknown_option_in_a_mapped_section(self):
        with pytest.raises(ConfigError, match=r"\[tiss\].*ical_urls"):
            from_dict({"tiss": {"ical_urls": "x"}})

    def test_a_section_given_as_a_value(self):
        """TOML will happily accept `semester = "2026W"`, which means something else."""
        with pytest.raises(ConfigError, match="should be a section"):
            from_dict({"semester": "2026W"})

    def test_every_unknown_key_is_reported_not_just_the_first(self):
        with pytest.raises(ConfigError) as err:
            from_dict({"wrong_one": 1, "wrong_two": 2})
        assert "wrong_one" in str(err.value) and "wrong_two" in str(err.value)


class TestScopedReminders:
    def test_falls_back_to_the_general_setting(self):
        r = Reminders(registration_minutes_before=(1440, 0))
        assert r.for_scope("exam") == (1440, 0)
        assert r.for_scope("group") == (1440, 0)

    def test_a_scope_override_wins(self):
        """Exercise groups are first-come-first-served; only the opening moment helps."""
        r = Reminders(registration_minutes_before=(1440, 0),
                      group_registration_minutes_before=(0,))
        assert r.for_scope("group") == (0,)
        assert r.for_scope("exam") == (1440, 0)

    def test_an_empty_override_is_honoured_not_treated_as_missing(self):
        """`group_registration_minutes_before = []` means "no reminder", and `or` would
        have quietly turned that back into the default."""
        r = Reminders(registration_minutes_before=(1440,),
                      group_registration_minutes_before=())
        assert r.for_scope("group") == ()

    def test_an_unknown_scope_does_not_explode(self):
        assert Reminders().for_scope("nonsense") == Reminders().registration_minutes_before


def test_settings_replace_keeps_the_original_untouched():
    s = from_dict({"courses": ["186.814"]})
    other = s.replace(courses=("194.187",))
    assert s.courses == ("186.814",)
    assert other.courses == ("194.187",)


def test_the_example_config_loads(tmp_path):
    """config.example.toml is the entry point for a fresh setup, and it has rotted
    before. If it stops being valid, this fails rather than the next person's first run."""
    import tomllib
    from pathlib import Path

    raw = tomllib.loads(
        (Path(__file__).resolve().parent.parent / "config.example.toml")
        .read_text(encoding="utf-8"))
    settings = from_dict(raw)
    assert isinstance(settings, Settings)
