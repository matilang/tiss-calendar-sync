"""Writing settings back to the file without throwing away what it explains.

Most of settings.toml is comments, and they are the reason the file is worth having in git:
why placeholder_min_hours is 4, why a colour was picked, why the profile must not change.
A writer that regenerated the file from a Settings object would produce a correct file and
destroy all of that - which is a bad trade, so this edits the document in place with
tomlkit and leaves everything it does not need to touch exactly as it was.

Three rules make that work:

    only differences are written   a value that already matches is not touched, so saving
                                   with nothing changed leaves the file byte-identical

    arrays are edited per item     removing one course keeps the comments on the other
                                   five. Only a genuine reorder falls back to replacing
                                   the whole array, and then the comments are lost

    secrets are never written      ical_url and calendar_id belong in .secrets.toml; a
                                   Settings carries them resolved, and writing them back
                                   would quietly undo the whole split

The file is validated through the strict loader before it replaces the original, and the
replacement is atomic, because this is the one file the user is expected to have edited by
hand and a half-written one would be worse than a failed save.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import fields, is_dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import tomlkit

from .config import SECTIONS, ConfigError, Settings, from_dict

# Resolved from .secrets.toml or the environment, never written back here.
SECRET_FIELDS = ("ical_url", "calendar_id")

# Plain fields on Settings that the file nonetheless writes as [sections].
#
# `apply` names them so the table is created before the keys go in. That turns out not to
# be load-bearing: assigning a dict to a tomlkit document produces the same [section] at
# the end of the file either way, and emptying this tuple breaks no test - checked, after
# adding `groups` to it on the assumption that it mattered. The branch stays because it
# states the intent at the point it applies, and because the alternative depends on a
# tomlkit convenience rather than on anything this module asks for.
#
# What does depend on this tuple is tests/test_config.py, which requires every section the
# loader understands to be documented in settings.toml. SECTIONS covers the dataclasses;
# this covers the two that are not.
TABLE_FIELDS = ("titles", "groups")


class WriteError(Exception):
    """The settings cannot be written, with a message meant for a human."""


def _plain(value: Any) -> Any:
    """Tuples to lists, dataclasses to dicts - TOML's shapes, comparable by ==."""
    if is_dataclass(value):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    return value


def as_dict(settings: Settings) -> dict[str, Any]:
    """Settings in the shape the TOML file has, minus the secrets, JSON-safe.

    Round-trips: from_dict(as_dict(s)) == s for everything except the two secret fields,
    which is what lets the interface send settings to a browser and get them back without
    either side knowing the schema.
    """
    out = {k: v for k, v in _plain(settings).items() if k not in SECRET_FIELDS}
    for section in out.values():
        if isinstance(section, dict):
            for key, value in section.items():
                if isinstance(value, date):
                    section[key] = value.isoformat()
    return out


def differences(text: str, new: Settings) -> dict[str, tuple[Any, Any]]:
    """What would change, as {dotted key: (from, to)}. Empty means nothing to write.

    Compared against the Settings the file currently yields, not against the raw file, so
    a value that merely restates a default counts as unchanged and is not written in.
    """
    current = from_dict(tomllib.loads(text), profile=new.profile)
    if new.profile != current.profile:
        # The tag is derived from this, and every event already on the calendar carries
        # it. Changing it here would orphan all of them, silently.
        raise WriteError(
            f"refusing to change the profile from {current.profile!r} to "
            f"{new.profile!r}: the events already on the calendar are tagged with it and "
            f"would be left with no owner. Edit the file by hand if you really mean it.")

    out: dict[str, tuple[Any, Any]] = {}
    for f in fields(Settings):
        if f.name in SECRET_FIELDS or f.name == "profile":
            continue
        old_value, new_value = _plain(getattr(current, f.name)), _plain(getattr(new, f.name))
        if old_value == new_value:
            continue
        if f.name in SECTIONS:
            for key in sorted(set(old_value) | set(new_value)):
                if old_value.get(key) != new_value.get(key):
                    out[f"{f.name}.{key}"] = (old_value.get(key), new_value.get(key))
        else:
            out[f.name] = (old_value, new_value)
    return out


def _indent_of(array) -> str:
    """The indentation the array's existing items use, so an added one lines up."""
    for line in array.as_string().splitlines()[1:]:
        body = line.lstrip(" ")
        if body and not body.startswith("]"):
            return line[:len(line) - len(body)]
    return "  "


def _set_array(array, values: list, notes: Mapping[str, str] | None = None) -> None:
    """Make a tomlkit array hold `values`, keeping per-item comments where possible.

    settings.toml annotates its course list item by item ("186.814",  # VU Algorithmics),
    and replacing the array wholesale would delete every one of those notes. Removing and
    appending individual items keeps them. A real reorder cannot be expressed that way, so
    it falls back to replacing - and loses the comments, which is unavoidable and rare.

    `notes` annotates what is *added*. Without it the file slowly loses its own
    documentation: every hand-written course carries its name in a comment, and a course
    added through the interface arrived as a bare number, so the more the page was used the
    less the file explained. Only applied to arrays that are already written one item per
    line - putting a comment in a single-line array would force it to grow.
    """
    old = list(array)
    kept = [v for v in old if v in values]
    added = [v for v in values if v not in old]
    if kept + added != values:
        del array[:]
        for v in values:
            array.append(v)
        return

    for v in old:
        if v not in values:
            array.remove(v)

    multiline = "\n" in array.as_string()
    indent = _indent_of(array)
    for v in added:
        note = (notes or {}).get(v)
        if note and multiline:
            array.add_line(v, indent=indent, comment=note)
        else:
            array.append(v)


def _assign(container, key: str, value: Any, notes: Mapping[str, str] | None = None) -> None:
    existing = container.get(key)
    if isinstance(value, list) and isinstance(existing, list):
        _set_array(existing, value, notes)
    elif isinstance(value, dict) and isinstance(existing, (dict, tomlkit.items.Table)):
        for k in [k for k in existing if k not in value]:
            del existing[k]
        for k, v in value.items():
            if existing.get(k) != v:
                existing[k] = v
    else:
        container[key] = value


def apply(text: str, new: Settings, *,
          notes: Mapping[str, Mapping[str, str]] | None = None) -> str:
    """The file's text with `new` written into it. Unchanged values are left alone.

    `notes` maps a dotted key to {value: comment}, used to annotate newly added list items -
    see _set_array.
    """
    changes = differences(text, new)
    if not changes:
        return text

    notes = notes or {}
    doc = tomlkit.parse(text)
    for dotted, (_, value) in changes.items():
        section, _, key = dotted.partition(".")
        note = notes.get(dotted)
        if key:
            if section not in doc:
                doc[section] = tomlkit.table()
            _assign(doc[section], key, value, note)
        elif section in TABLE_FIELDS:
            if section not in doc:
                doc[section] = tomlkit.table()
            _assign(doc, section, value, note)
        else:
            _assign(doc, section, value, note)

    out = tomlkit.dumps(doc)

    # Validate before this can reach the disk: a writer that produces a file the loader
    # rejects would leave the project unable to start, and the cause two steps back.
    try:
        from_dict(tomllib.loads(out), profile=new.profile)
    except (ConfigError, tomllib.TOMLDecodeError) as e:
        raise WriteError(f"the edit would produce a settings file that cannot be read: {e}")
    return out


def write(path: Path, new: Settings, *,
          notes: Mapping[str, Mapping[str, str]] | None = None) -> dict[str, tuple[Any, Any]]:
    """Save `new` into `path`, returning what changed. Writes nothing if nothing changed.

    Atomic: the new text goes to a temporary file in the same directory and then replaces
    the original in one step, so an interrupted save cannot leave a truncated settings
    file. Bytes are written explicitly to keep the file's LF endings on Windows.
    """
    text = path.read_text(encoding="utf-8")
    changes = differences(text, new)
    if not changes:
        return {}

    out = apply(text, new, notes=notes)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(out.encode("utf-8"))
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return changes
