"""The configuration, as types rather than a dictionary.

Every option lives here exactly once: its name, its type and its default. Before this,
defaults were duplicated between the loader and the call sites - `exam_keywords` had one
in config.toml and another inside is_exam - so changing a default meant finding every
copy, and a misspelled key did nothing at all, silently.

Unknown keys are now an error. A typo like `placeholder_min_hour` used to leave the real
setting at its default and give no hint; it now names the offending key and, where it can,
the one you probably meant.
"""
from __future__ import annotations

import difflib
import sys
import tomllib
from dataclasses import dataclass, field, fields, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

SOURCE_TAG = "tiss_sync"


class ConfigError(Exception):
    """Something in the config file is wrong, with a message meant for a human."""


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Semester:
    """The window lectures are kept in. Scraped exams deliberately ignore it."""
    start: date = field(default_factory=date.today)
    end: date = field(default_factory=lambda: date.today() + timedelta(days=200))


@dataclass(frozen=True)
class Exercises:
    # Courses whose exercise slots are hidden until you have picked a group.
    hide_for: tuple[str, ...] = ()
    # What counts as an exercise. Q&A deliberately does not match.
    keywords: tuple[str, ...] = ("Exercise", "Übung", "Uebung")
    # An exercise of a VU is still an Übung: "VU Algorithmics" -> "UE Algorithmics".
    type_code: str = "UE"


@dataclass(frozen=True)
class Retention:
    prune_past: bool = False
    keep_past_kinds: tuple[str, ...] = ("exam",)
    # TISS writes the same session both as "Algorithmics Q&A" and as "Q & A 2".
    keep_past_keywords: tuple[str, ...] = ("Exercise", "Q&A", "Q & A", "Test", "Prüfung")


@dataclass(frozen=True)
class Scrape:
    courses: tuple[str, ...] = ()
    semester: str = "2026W"
    registration_kinds: tuple[str, ...] = ("exam", "course", "group")
    registration_close_reminder: bool = True


@dataclass(frozen=True)
class Reminders:
    lecture_minutes_before: tuple[int, ...] = (15,)
    exam_minutes_before: tuple[int, ...] = (3 * 24 * 60, 24 * 60)
    exam_keywords: tuple[str, ...] = ("Prüfung", "Exam", "Test", "Klausur", "Abgabe",
                                      "Deadline")
    exam_color_id: str = "11"            # red
    registration_minutes_before: tuple[int, ...] = (24 * 60, 0)
    registration_color_id: str = "5"     # banana
    # Per-scope overrides. An exercise group is first-come-first-served and opens at a
    # published hour, so a day-early nudge is useless there; exam sign-up wants one.
    exam_registration_minutes_before: tuple[int, ...] | None = None
    course_registration_minutes_before: tuple[int, ...] | None = None
    group_registration_minutes_before: tuple[int, ...] | None = None

    def for_scope(self, scope: str) -> tuple[int, ...]:
        override = getattr(self, f"{scope}_registration_minutes_before", None)
        return override if override is not None else self.registration_minutes_before


# --------------------------------------------------------------------------- #
# The whole thing
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Settings:
    ical_url: str = ""
    calendar_id: str = ""
    courses: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    # Timed events at least this long are unregistered group placeholders, not lectures.
    # 0 disables the rule.
    placeholder_min_hours: float = 4
    merge_parallel_rooms: bool = True
    titles: Mapping[str, str] = field(default_factory=dict)
    semester: Semester = field(default_factory=Semester)
    exercises: Exercises = field(default_factory=Exercises)
    retention: Retention = field(default_factory=Retention)
    scrape: Scrape = field(default_factory=Scrape)
    reminders: Reminders = field(default_factory=Reminders)
    # Marks the events this config owns. Two configs never delete each other's events.
    tag: str = f"{SOURCE_TAG}-config"

    def replace(self, **changes: Any) -> "Settings":
        """A copy with some fields changed - handy in tests, and keeps this immutable."""
        return replace(self, **changes)


SECTIONS: dict[str, type] = {
    "semester": Semester,
    "exercises": Exercises,
    "retention": Retention,
    "scrape": Scrape,
    "reminders": Reminders,
}

# Keys that live in a [section] of the file but are plain fields on Settings.
NESTED_FIELDS = {"tiss": {"ical_url"}, "google": {"calendar_id"}}


def _suggest(key: str, known: list[str]) -> str:
    close = difflib.get_close_matches(key, known, n=1, cutoff=0.7)
    return f" - did you mean {close[0]!r}?" if close else ""


def _build(cls: type, raw: Mapping[str, Any], where: str) -> Any:
    known = [f.name for f in fields(cls)]
    unknown = [k for k in raw if k not in known]
    if unknown:
        raise ConfigError("\n".join(
            f"unknown option {where}{k!r}{_suggest(k, known)}" for k in unknown))

    values: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in raw:
            continue
        value = raw[f.name]
        if isinstance(value, list):
            value = tuple(value)
        if f.type.startswith("date") and isinstance(value, str):
            value = date.fromisoformat(value)
        values[f.name] = value
    return cls(**values)


def from_dict(raw: Mapping[str, Any], tag: str = f"{SOURCE_TAG}-config") -> Settings:
    """Settings from parsed TOML, rejecting anything it does not recognise."""
    raw = dict(raw)
    flat_known = [f.name for f in fields(Settings)]
    allowed = set(flat_known) | set(SECTIONS) | set(NESTED_FIELDS)

    unknown = [k for k in raw if k not in allowed]
    if unknown:
        raise ConfigError("\n".join(
            f"unknown option {k!r}{_suggest(k, sorted(allowed))}" for k in unknown))

    values: dict[str, Any] = {"tag": tag}

    for section, inner_keys in NESTED_FIELDS.items():
        inner = raw.pop(section, {})
        if not isinstance(inner, dict):
            raise ConfigError(f"[{section}] should be a section, not a value")
        for key in inner:
            if key not in inner_keys:
                raise ConfigError(
                    f"unknown option [{section}].{key!r}{_suggest(key, sorted(inner_keys))}")
        values.update(inner)

    for name, cls in SECTIONS.items():
        if name in raw:
            inner = raw.pop(name)
            if not isinstance(inner, dict):
                raise ConfigError(f"[{name}] should be a section, not a value")
            values[name] = _build(cls, inner, f"[{name}].")

    for key, value in raw.items():
        values[key] = tuple(value) if isinstance(value, list) else value

    return Settings(**values)


def load_config(path: Path) -> Settings:
    """Read a config file. Exits with a readable message rather than a traceback."""
    if not path.exists():
        sys.exit(f"Config not found: {path}\n"
                 f"Copy config.example.toml to config.toml first.")
    with path.open("rb") as f:
        raw = tomllib.load(f)
    try:
        # The tag is the file's own name, so config.toml and tuwel.toml own separate
        # events on the same calendar and neither can delete the other's.
        return from_dict(raw, tag=f"{SOURCE_TAG}-{path.stem}")
    except ConfigError as e:
        sys.exit(f"{path}:\n{e}")
