"""Writing settings back without destroying the file.

settings.toml is two thirds comments, and those comments are why the file is worth keeping
in git. The headline test here is the dull one: saving with nothing changed must leave the
file byte-identical. Everything else is a way for that to stop being true.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from tisscal.config import from_dict
from tisscal.settings_io import WriteError, apply, differences, write
from conftest import shift

ROOT = Path(__file__).resolve().parent.parent
COMMITTED = ("settings.toml", "settings.tuwel.toml")


def load(path: Path):
    """The Settings a file yields, without touching .secrets.toml."""
    return from_dict(tomllib.loads(path.read_text(encoding="utf-8")), profile=path.stem)


def comments(text: str) -> list[str]:
    return [l.strip() for l in text.splitlines() if l.strip().startswith("#")]


# Most of this module edits the project's real settings.toml, because that is the file the
# writer has to preserve and a hand-made sample would not have its comments, its ordering
# or its annotated arrays. The cost is that a test naming a value from it is a test that
# fails when the file is legitimately edited - a course finished, a group picked - and a
# red build for using the tool correctly teaches people to ignore the build.
#
# So nothing below names a course it expects to find. Targets are taken from the file, and
# the one value this module does name is ABSENT, which exists to be absent and says so in
# its own test.
def inline_note(text: str, value: str) -> str | None:
    """The trailing comment on the array line holding `value`, if it has one."""
    for line in text.splitlines():
        if f'"{value}"' in line and "#" in line:
            return line.split("#", 1)[1].strip()
    return None


class TestNothingToWrite:
    @pytest.mark.parametrize("name", COMMITTED)
    def test_saving_an_unchanged_file_leaves_it_byte_identical(self, name):
        """The guard the rest of this module exists for. Anything that reformats, reorders
        or re-serialises shows up here as a diff on a save that should be a no-op."""
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        assert apply(text, load(path)) == text

    @pytest.mark.parametrize("name", COMMITTED)
    def test_a_file_differs_from_itself_in_nothing(self, name):
        path = ROOT / name
        assert differences(path.read_text(encoding="utf-8"), load(path)) == {}

    def test_write_touches_nothing_and_reports_nothing(self, tmp_path):
        path = tmp_path / "settings.toml"
        path.write_bytes((ROOT / "settings.toml").read_bytes())
        before = path.read_bytes()
        assert write(path, load(path)) == {}
        assert path.read_bytes() == before

    def test_a_value_equal_to_the_default_is_not_written_in(self, tmp_path):
        """Restating a default would grow the file every time anything was saved."""
        path = tmp_path / "s.toml"
        path.write_text('courses = ["186.814"]\n', encoding="utf-8")
        assert write(path, load(path)) == {}
        assert path.read_text(encoding="utf-8") == 'courses = ["186.814"]\n'


class TestCommentsSurvive:
    def test_every_comment_line_survives_an_edit(self, tmp_path):
        text = (ROOT / "settings.toml").read_text(encoding="utf-8")
        new = shift(load(ROOT / "settings.toml"), exercises={"color_id": "3"},
                    courses=["194.187", "186.814"])
        out = apply(text, new)
        lost = set(comments(text)) - set(comments(out))
        assert not lost, f"these comment lines disappeared: {sorted(lost)[:3]}"

    def test_removing_one_course_keeps_the_others_comments(self, tmp_path):
        """The course list is annotated per item. Replacing the array wholesale would be
        the obvious implementation and would delete five explanations to remove one line.

        The course removed is whichever one the file lists first, so this keeps testing a
        real removal as the list changes. Naming one used to make it test nothing: it named
        193.219, which has not been in [courses] for some time, so the removal was a no-op
        and the assertions passed on an unmodified file.
        """
        path = ROOT / "settings.toml"
        text = path.read_text(encoding="utf-8")
        courses = list(load(path).courses)
        assert len(courses) > 1, "needs a list with something left after a removal"
        victim, kept = courses[0], courses[1:]

        out = apply(text, shift(load(path), courses=kept))

        assert f'"{victim}"' not in out.split("]")[0], "the removal reached the array"
        for course in kept:
            note = inline_note(text, course)
            if note:
                assert note in out, f"{course} lost its comment: {note}"

    def test_an_inline_comment_survives_its_value_changing(self, tmp_path):
        path = ROOT / "settings.toml"
        text = path.read_text(encoding="utf-8")
        titles = dict(load(path).titles)
        annotated = [nr for nr in titles if inline_note(text, nr)]
        assert annotated, "[titles] has no annotated entry left to test with"

        nr = annotated[0]
        out = apply(text, shift(load(path), titles={**titles, nr: "VU Renamed"}))
        assert f'"{nr}" = "VU Renamed"' in out
        assert inline_note(text, nr) in out


class TestArrays:
    ABSENT = "184.702"

    def test_the_assumption_this_rests_on(self):
        assert self.ABSENT not in load(ROOT / "settings.toml").courses

    def test_an_item_can_be_appended(self, tmp_path):
        path = ROOT / "settings.toml"
        new = shift(load(path), courses=list(load(path).courses) + [self.ABSENT])
        out = apply(path.read_text(encoding="utf-8"), new)
        assert self.ABSENT in out
        assert load_text(out).courses[-1] == self.ABSENT

    def test_a_reorder_is_written_even_though_the_comments_cannot_survive(self, tmp_path):
        """Documented trade-off: per-item editing cannot express a reorder, so the array
        is replaced. The values must still be right - silently ignoring the change would
        leave Settings and the file disagreeing."""
        path = ROOT / "settings.toml"
        reversed_courses = list(reversed(load(path).courses))
        out = apply(path.read_text(encoding="utf-8"), shift(load(path),
                                                            courses=reversed_courses))
        assert list(load_text(out).courses) == reversed_courses

    def test_emptying_an_array(self, tmp_path):
        path = ROOT / "settings.toml"
        out = apply(path.read_text(encoding="utf-8"), shift(load(path), courses=[]))
        assert load_text(out).courses == ()


class TestSecretsAreNeverWritten:
    def test_a_resolved_feed_url_does_not_reach_the_file(self, tmp_path):
        """Settings carries ical_url resolved from .secrets.toml. Writing it back would
        put a token into a committed file and quietly undo the whole split."""
        path = ROOT / "settings.toml"
        text = path.read_text(encoding="utf-8")
        loaded = load(path).replace(ical_url="https://tiss.invalid/f?token=secret",
                                    calendar_id="x@group.calendar.google.com")
        assert differences(text, loaded) == {}
        out = apply(text, loaded)
        assert "token=secret" not in out
        assert "ical_url" not in out and "calendar_id" not in out


class TestRefusals:
    def test_changing_the_profile_is_refused(self, tmp_path):
        """The tag is derived from it and every event on the calendar carries it, so a
        rename here would orphan all of them without failing."""
        path = ROOT / "settings.toml"
        with pytest.raises(WriteError, match="profile"):
            differences(path.read_text(encoding="utf-8"),
                        load(path).replace(profile="tiss"))

    def test_the_refusal_explains_the_consequence(self):
        path = ROOT / "settings.toml"
        with pytest.raises(WriteError, match="no owner"):
            differences(path.read_text(encoding="utf-8"),
                        load(path).replace(profile="tiss"))


class TestTheTomlTrap:
    """A bare key written after the first [table] header silently becomes part of that
    table. settings.toml warns about it in a comment; the writer must not fall into it."""

    def test_a_new_top_level_key_does_not_land_inside_a_section(self, tmp_path):
        path = tmp_path / "s.toml"
        path.write_text('profile = "config"\n\n[reminders]\nexam_color_id = "11"\n',
                        encoding="utf-8")
        out = apply(path.read_text(encoding="utf-8"),
                    shift(load(path), courses=["186.814"], placeholder_min_hours=6))
        raw = tomllib.loads(out)
        assert raw["courses"] == ["186.814"]
        assert raw["placeholder_min_hours"] == 6
        assert "courses" not in raw["reminders"]
        assert "placeholder_min_hours" not in raw["reminders"]

    def test_a_missing_section_is_created(self, tmp_path):
        path = tmp_path / "s.toml"
        path.write_text('courses = ["186.814"]\n', encoding="utf-8")
        out = apply(path.read_text(encoding="utf-8"),
                    shift(load(path), exercises={"keywords": ["Exercise"]}))
        raw = tomllib.loads(out)
        assert raw["exercises"]["keywords"] == ["Exercise"]
        assert raw["courses"] == ["186.814"]


class TestTitles:
    ABSENT = "184.702"

    def test_the_assumption_this_rests_on(self):
        assert self.ABSENT not in load(ROOT / "settings.toml").titles

    def test_a_title_can_be_added(self):
        path = ROOT / "settings.toml"
        new = shift(load(path), titles={**load(path).titles, self.ABSENT: "VU X"})
        out = apply(path.read_text(encoding="utf-8"), new)
        assert load_text(out).titles[self.ABSENT] == "VU X"

    def test_a_title_can_be_removed(self):
        """Removes whichever title the file happens to list first.

        It used to remove 193.219 by name - a course waiting to drop out of the feed, so
        this test was scheduled to fail on the day that happened, for no reason connected
        to the writer.
        """
        path = ROOT / "settings.toml"
        titles = dict(load(path).titles)
        assert titles, "[titles] is empty, so there is nothing to remove"
        victim = next(iter(titles))
        del titles[victim]

        out = apply(path.read_text(encoding="utf-8"), shift(load(path), titles=titles))
        assert victim not in load_text(out).titles


class TestGroups:
    """[groups] is written through the interface's dropdown.

    It is the first setting whose value nobody can derive - which group you registered for
    is not in the feed anywhere - so the path from a dropdown to the file is all there is.

    Nothing here asserts what [groups] currently holds. The first version of this class
    did, and it failed the first time the feature was used for real: a test that pins down
    a value the user is *expected* to change turns using the tool into a red build. The
    rule it broke is the same one TestAnnotatesAdditions follows below - a test may state
    an assumption about settings.toml and fail loudly if it stops holding, but it may not
    quietly depend on one.
    """
    ABSENT = "184.702"

    def test_the_assumption_this_rests_on(self):
        assert self.ABSENT not in load(ROOT / "settings.toml").groups

    def _with_group(self, value: str, *, text: str | None = None) -> str:
        path = ROOT / "settings.toml"
        text = text if text is not None else path.read_text(encoding="utf-8")
        current = load_text(text)
        return apply(text, shift(current, groups={**current.groups, self.ABSENT: value}))

    def test_a_group_can_be_chosen(self):
        assert load_text(self._with_group("B")).groups[self.ABSENT] == "B"

    @pytest.mark.parametrize("field, value",
                             [("groups", {"192.216": "B"}), ("titles", {"192.216": "EoAI"})])
    def test_the_section_is_created_when_the_file_has_none(self, tmp_path, field, value):
        """Both of these are plain fields on Settings that the file writes as sections.

        Every other test here edits the project's own settings.toml, which already has
        both sections, so none of them covered writing one that is not there - and that is
        not a hypothetical file: settings.tuwel.toml has neither, nor does any settings
        file written before an option existed.

        What this does not test is TABLE_FIELDS, though it was written to. Emptying that
        tuple leaves every test green, because assigning a dict to a tomlkit document
        yields the same [section] regardless. The guarantee worth having is the one
        asserted below: the value ends up in a section of its own and not as a bare key
        swallowed by the preceding one - which is the trap settings.toml's own header
        warns about, and the thing that would actually corrupt a config.
        """
        path = tmp_path / "settings.toml"
        path.write_text('courses = ["186.814"]\n\n[semester]\nstart = "2026-10-01"\n',
                        encoding="utf-8")
        text = path.read_text(encoding="utf-8")
        assert f"[{field}]" not in text

        out = apply(text, shift(load(path), **{field: value}))
        assert f"[{field}]" in out, f"{field} was not written as a section"
        assert getattr(load_text(out), field) == value
        assert load_text(out).semester.start.isoformat() == "2026-10-01", \
            "the value did not land inside the preceding section"

    def test_choosing_one_leaves_the_other_courses_alone(self):
        """The whole file is rewritten on every save, so "it only changed what I picked"
        is a property worth asserting rather than assuming."""
        before = load(ROOT / "settings.toml").groups
        after = load_text(self._with_group("B")).groups
        assert {k: v for k, v in after.items() if k != self.ABSENT} == dict(before)

    def test_choosing_a_group_keeps_the_comments_explaining_the_section(self):
        """Those comments are the only place that says why the setting exists at all, and
        the section is short enough that a writer could plausibly replace it wholesale."""
        text = (ROOT / "settings.toml").read_text(encoding="utf-8")
        assert set(comments(text)) <= set(comments(self._with_group("B")))

    def test_a_group_can_be_changed_and_cleared(self):
        text = self._with_group("B")
        text = self._with_group("A", text=text)
        assert load_text(text).groups[self.ABSENT] == "A"

        current = load_text(text)
        cleared = {k: v for k, v in current.groups.items() if k != self.ABSENT}
        out = apply(text, shift(current, groups=cleared))
        assert self.ABSENT not in load_text(out).groups


class TestWritingToDisk:
    def test_the_result_is_always_loadable(self, tmp_path):
        """A writer that can produce a file the loader rejects would leave the project
        unable to start, with the cause two steps back."""
        path = tmp_path / "settings.toml"
        path.write_bytes((ROOT / "settings.toml").read_bytes())
        write(path, shift(load(path), retention={"prune_past": False},
                          scrape={"registration_kinds": ["exam"]}))
        assert from_dict(tomllib.loads(path.read_text(encoding="utf-8")))

    def test_line_endings_stay_lf(self, tmp_path):
        """write_text would rewrite every line ending to the platform default; that has
        already damaged a file in this repository twice."""
        path = tmp_path / "settings.toml"
        path.write_bytes((ROOT / "settings.toml").read_bytes())
        write(path, shift(load(path), courses=["186.814"]))
        assert b"\r\n" not in path.read_bytes()

    def test_it_reports_what_changed(self, tmp_path):
        path = tmp_path / "settings.toml"
        path.write_bytes((ROOT / "settings.toml").read_bytes())
        changed = write(path, shift(load(path), exercises={"color_id": "3"},
                                    merge_parallel_rooms=False))
        assert changed["exercises.color_id"] == ("7", "3")
        assert changed["merge_parallel_rooms"] == (True, False)

    def test_no_temporary_file_is_left_behind(self, tmp_path):
        path = tmp_path / "settings.toml"
        path.write_bytes((ROOT / "settings.toml").read_bytes())
        write(path, shift(load(path), courses=["186.814"]))
        assert list(p.name for p in tmp_path.iterdir()) == ["settings.toml"]

    def test_the_change_is_what_comes_back_out(self, tmp_path):
        """The round trip that matters in practice: save, reload, get what you asked for."""
        path = tmp_path / "settings.toml"
        path.write_bytes((ROOT / "settings.toml").read_bytes())
        write(path, shift(load(path), exercises={"color_id": "3"}))
        assert load(path).exercises.color_id == "3"


def load_text(text: str):
    return from_dict(tomllib.loads(text))


class TestAnnotatingWhatIsAdded:
    """Every course in settings.toml was written by hand with its name beside it. A course
    ticked in the interface arrived as a bare number, so the file explained a little less
    each time the page was used - a slow loss that nothing would ever have reported."""

    # A course number the committed settings do not have. Asserted rather than assumed: the
    # first version of these tests added 192.216, which the working copy had just gained, so
    # "add it" was a no-op and the test failed for a reason that had nothing to do with it.
    ABSENT = "184.702"
    NOTE = "VU Advanced Internet Computing"

    def test_the_assumption_these_tests_rest_on(self):
        assert self.ABSENT not in load(ROOT / "settings.toml").courses

    def with_it_added(self, notes=None):
        path = ROOT / "settings.toml"
        new = shift(load(path), courses=list(load(path).courses) + [self.ABSENT])
        return apply(path.read_text(encoding="utf-8"), new, notes=notes)

    def test_an_added_item_carries_its_note(self):
        out = self.with_it_added({"courses": {self.ABSENT: self.NOTE}})
        assert f'"{self.ABSENT}",' in out
        assert f"# {self.NOTE}" in out

    def test_an_item_without_a_note_is_still_added(self):
        assert self.ABSENT in load_text(self.with_it_added({"courses": {}})).courses

    def test_the_other_notes_still_survive(self):
        text = (ROOT / "settings.toml").read_text(encoding="utf-8")
        out = self.with_it_added({"courses": {self.ABSENT: self.NOTE}})
        assert not set(comments(text)) - set(comments(out))

    def test_it_lines_up_with_the_items_above(self):
        out = self.with_it_added({"courses": {self.ABSENT: self.NOTE}})
        added = [l for l in out.splitlines() if f'"{self.ABSENT}"' in l][0]
        neighbour = [l for l in out.splitlines() if '"186.814",' in l][0]
        assert len(added) - len(added.lstrip()) == len(neighbour) - len(neighbour.lstrip())

    def test_the_result_still_loads(self):
        out = self.with_it_added({"courses": {self.ABSENT: self.NOTE}})
        assert self.ABSENT in from_dict(tomllib.loads(out)).courses

    def test_a_single_line_array_is_not_forced_open(self, tmp_path):
        """exclude_keywords = ["Sprechstunde"] is one line and should stay one line; a
        comment cannot go into it without breaking it apart."""
        path = tmp_path / "s.toml"
        path.write_text('exclude_keywords = ["Sprechstunde"]\n', encoding="utf-8")
        out = apply(path.read_text(encoding="utf-8"),
                    shift(load(path), exclude_keywords=["Sprechstunde", "Tutorium"]),
                    notes={"exclude_keywords": {"Tutorium": "a note that cannot fit"}})
        assert out.count("\n") == 1
        assert "a note that cannot fit" not in out
        assert load_text(out).exclude_keywords == ("Sprechstunde", "Tutorium")
