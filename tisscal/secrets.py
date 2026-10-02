"""Where the private values come from, separately from everything else.

Two values in this project are private: the iCal URL, whose query string carries a TISS
feed token or a TUWEL authtoken, and the calendar id. Everything else - which courses,
what they are called, which reminders, what gets pruned - is ordinary configuration that
is better off in git, where a change is a diff with a date and a reason on it.

Before this split they lived in one file, so the file could not be committed, so the
whole configuration had to be pasted into a GitHub secret as well. Adding a course meant
editing the file and remembering to re-paste the secret; forgetting the second half left
the cloud running yesterday's course list with no sign anything was stale.

Three places are searched, first one wins:

    1. the environment   TISSCAL_ICAL_URL, TISSCAL_CALENDAR_ID
    2. .secrets.toml     next to the settings file, never committed
    3. the settings file itself, so an older single-file config keeps working

In .secrets.toml a bare key applies to every profile and a [profile] section overrides
it, which is what you want for the calendar id: one calendar, several feeds.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Mapping

# The fields that may be supplied this way. Deliberately short: a value only belongs here
# if it is private, otherwise it belongs in the committed settings file where it is
# reviewable.
SECRET_FIELDS = ("ical_url", "calendar_id")

ENV_PREFIX = "TISSCAL_"
SECRETS_FILE = ".secrets.toml"


def env_name(field: str) -> str:
    """TISSCAL_ICAL_URL for ical_url. One name per field, no per-profile variants: the
    cloud workflow runs one profile per step and sets the environment for that step."""
    return ENV_PREFIX + field.upper()


def read_file(path: Path) -> Mapping[str, Any]:
    """Parse .secrets.toml, or return nothing if there is none - it is optional."""
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def validate(raw: Mapping[str, Any], where: str = SECRETS_FILE) -> None:
    """Reject anything that is not a secret field or a [profile] section.

    Worth doing even for two keys: a mistyped `ical_urls` here would leave the real URL
    to be found in the settings file, so the sync would keep working while the secret
    sat unused - exactly the kind of thing that is only noticed once the token is
    rotated and nothing updates.
    """
    from .config import ConfigError, _suggest

    for key, value in raw.items():
        if isinstance(value, dict):
            for inner in value:
                if inner not in SECRET_FIELDS:
                    raise ConfigError(
                        f"{where}: [{key}] has no option {inner!r}"
                        f"{_suggest(inner, list(SECRET_FIELDS))}")
        elif key not in SECRET_FIELDS:
            raise ConfigError(f"{where}: unknown option {key!r}"
                              f"{_suggest(key, list(SECRET_FIELDS))}")


def resolve(profile: str, *, secrets: Mapping[str, Any] | None = None,
            env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The secrets that apply to one profile, highest precedence first.

    Only the fields actually found are returned, so a caller can tell "not set here"
    from "set to empty" and fall back to the settings file.
    """
    env = os.environ if env is None else env
    secrets = secrets or {}
    section = secrets.get(profile) or {}

    found: dict[str, str] = {}
    for field in SECRET_FIELDS:
        for value in (env.get(env_name(field)), section.get(field), secrets.get(field)):
            if value:
                found[field] = value
                break
    return found


def sources(field: str, profile: str) -> str:
    """The three places a missing value could have come from, for an error message."""
    return (f"  - the {env_name(field)} environment variable\n"
            f"  - {field} in {SECRETS_FILE}, under [{profile}] or at the top\n"
            f"  - {field} in the settings file itself")
