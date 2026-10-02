"""Settings in git, secrets out of it.

Two things are being guarded here. The obvious one is precedence: the environment over
.secrets.toml over the settings file, because the cloud run supplies its values through
the environment and a local run through the file, and both must work.

The less obvious one is that the split created a new way to fail. Before it, no settings
file was ever committed, so a token could not reach git by accident. Now two of them are
committed, and `test_committed_settings_hold_no_secrets` is what keeps a pasted URL from
riding along in a commit that looks like a course change.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tisscal import secrets
from tisscal.config import ConfigError, load_config

ROOT = Path(__file__).resolve().parent.parent
COMMITTED_SETTINGS = ("settings.toml", "settings.tuwel.toml", ".secrets.example.toml")

FEED = "https://tiss.tuwien.ac.at/feed?token=from-"
CAL = "cal-from-"


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def settings_file(tmp_path) -> Path:
    """A settings file with no secrets in it, as the committed ones are."""
    return write(tmp_path / "settings.toml",
                 'profile = "config"\ncourses = ["186.814"]\n')


class TestPrecedence:
    def test_environment_wins_over_the_file(self, tmp_path, settings_file):
        write(tmp_path / ".secrets.toml",
              f'[config]\nical_url = "{FEED}file"\ncalendar_id = "{CAL}file"\n')
        s = load_config(settings_file, env={"TISSCAL_ICAL_URL": f"{FEED}env",
                                           "TISSCAL_CALENDAR_ID": f"{CAL}env"})
        assert s.ical_url == f"{FEED}env"
        assert s.calendar_id == f"{CAL}env"

    def test_a_profile_section_wins_over_a_bare_key(self, tmp_path, settings_file):
        """The bare key is the shared default - one calendar, several feeds - so a
        profile that needs its own value has to be able to say so."""
        write(tmp_path / ".secrets.toml",
              f'ical_url = "{FEED}shared"\n\n[config]\nical_url = "{FEED}config"\n')
        assert load_config(settings_file, env={}).ical_url == f"{FEED}config"

    def test_a_bare_key_applies_to_every_profile(self, tmp_path):
        """What the calendar id is actually for: written once, used by both profiles."""
        write(tmp_path / ".secrets.toml",
              f'calendar_id = "{CAL}shared"\n\n[config]\nical_url = "{FEED}a"\n'
              f'\n[tuwel]\nical_url = "{FEED}b"\n')
        tiss = write(tmp_path / "settings.toml", 'profile = "config"\n')
        tuwel = write(tmp_path / "settings.tuwel.toml", 'profile = "tuwel"\n')
        assert load_config(tiss, env={}).calendar_id == f"{CAL}shared"
        assert load_config(tuwel, env={}).calendar_id == f"{CAL}shared"
        assert load_config(tuwel, env={}).ical_url == f"{FEED}b"

    def test_the_settings_file_is_still_honoured(self, tmp_path):
        """A single-file config from before the split has to keep working - there is one
        on this machine, and a sync that silently stopped reading it would be worse than
        one that failed."""
        old = write(tmp_path / "config.toml",
                    f'[tiss]\nical_url = "{FEED}inline"\n'
                    f'[google]\ncalendar_id = "{CAL}inline"\n')
        s = load_config(old, env={})
        assert s.ical_url == f"{FEED}inline"
        assert s.calendar_id == f"{CAL}inline"
        assert s.tag == "tiss_sync-config"  # from the file name, as it always was

    def test_an_empty_environment_variable_does_not_mask_the_file(self, tmp_path,
                                                                 settings_file):
        """CI sets a variable to "" when a secret is unset, which must not count as an
        answer - otherwise a missing secret would look like a missing feed."""
        write(tmp_path / ".secrets.toml", f'[config]\nical_url = "{FEED}file"\n')
        assert load_config(settings_file,
                           env={"TISSCAL_ICAL_URL": ""}).ical_url == f"{FEED}file"


class TestMissing:
    def test_no_ical_url_anywhere_names_all_three_places(self, settings_file):
        with pytest.raises(SystemExit) as err:
            load_config(settings_file, env={})
        message = str(err.value)
        assert "TISSCAL_ICAL_URL" in message
        assert ".secrets.toml" in message
        assert "[config]" in message

    def test_a_missing_secrets_file_is_fine(self, tmp_path, settings_file):
        """It is optional: the environment alone is enough, which is the cloud case."""
        assert not (tmp_path / ".secrets.toml").exists()
        s = load_config(settings_file, env={"TISSCAL_ICAL_URL": f"{FEED}env"})
        assert s.ical_url == f"{FEED}env"

    def test_a_missing_calendar_id_is_not_fatal_at_load_time(self, settings_file):
        """list, preview and export need no calendar; only sync does, and it checks."""
        s = load_config(settings_file, env={"TISSCAL_ICAL_URL": f"{FEED}env"})
        assert s.calendar_id == ""


class TestRejectsTypos:
    def test_unknown_key(self):
        with pytest.raises(ConfigError, match="ical_urls"):
            secrets.validate({"ical_urls": "x"})

    def test_unknown_key_inside_a_profile_section(self):
        with pytest.raises(ConfigError, match="calender_id"):
            secrets.validate({"config": {"calender_id": "x"}})

    def test_the_message_suggests_the_right_name(self):
        """A typo here is quiet in the worst way: the real URL is still found in the
        settings file, so the sync works and the secret sits unused until the token is
        rotated and nothing updates."""
        with pytest.raises(ConfigError, match="did you mean 'calendar_id'"):
            secrets.validate({"calender_id": "x"})

    def test_a_real_file_is_accepted(self):
        raw = secrets.read_file(ROOT / ".secrets.example.toml")
        secrets.validate(raw)  # must not raise


class TestProfileIdentity:
    """The tag decides which events a sync owns and may delete. It used to come from the
    file name, so this rename would have orphaned every event already on the calendar."""

    def test_an_explicit_profile_survives_a_rename(self, tmp_path):
        renamed = write(tmp_path / "settings.toml",
                        f'profile = "config"\n[tiss]\nical_url = "{FEED}x"\n')
        assert load_config(renamed, env={}).tag == "tiss_sync-config"

    def test_without_one_the_tag_follows_the_file_name(self, tmp_path):
        """Which is the old behaviour, kept for pre-split files - and the reason the
        committed files set `profile` explicitly."""
        plain = write(tmp_path / "whatever.toml", f'[tiss]\nical_url = "{FEED}x"\n')
        assert load_config(plain, env={}).tag == "tiss_sync-whatever"

    def test_the_two_profiles_do_not_share_a_tag(self):
        tiss = load_config(ROOT / "settings.toml",
                           secrets_path=ROOT / "nonexistent.toml",
                           env={"TISSCAL_ICAL_URL": f"{FEED}x"})
        tuwel = load_config(ROOT / "settings.tuwel.toml",
                            secrets_path=ROOT / "nonexistent.toml",
                            env={"TISSCAL_ICAL_URL": f"{FEED}x"})
        assert tiss.tag == "tiss_sync-config"
        assert tuwel.tag == "tiss_sync-tuwel"
        assert tiss.tag != tuwel.tag


class TestCommittedFilesAreClean:
    """The new failure mode. These files are in git, so anything private in them is
    published; a leak would arrive inside a commit that looks like a course change."""

    # A real feed token is a UUID and a TUWEL authtoken is 40 hex characters, so what a
    # leak looks like is a long run of token characters after the "=". Matching the value
    # rather than the key name is deliberate: .secrets.example.toml has to show the shape
    # of the URL, so the key names legitimately appear there.
    SECRET_VALUE = re.compile(
        r"(?:token|authtoken)=([A-Za-z0-9_-]{12,})|([a-z0-9]{24,})@group\.calendar\.google\.com")
    PLACEHOLDERS = ("PASTE", "xxxx", "YOUR")

    @pytest.mark.parametrize("name", COMMITTED_SETTINGS)
    def test_committed_settings_hold_no_secrets(self, name):
        text = (ROOT / name).read_text(encoding="utf-8")
        for match in self.SECRET_VALUE.finditer(text):
            value = match.group(1) or match.group(2)
            if not any(p in value for p in self.PLACEHOLDERS):
                pytest.fail(f"{name} line {text[:match.start()].count(chr(10)) + 1} "
                            f"looks like a real secret: {value[:6]}...")

    @pytest.mark.parametrize("name", ("settings.toml", "settings.tuwel.toml"))
    def test_the_settings_files_do_not_mention_the_private_fields(self, name):
        """Not even as a commented-out line: if the value belongs in .secrets.toml, so
        does the key, otherwise the next person fills in the wrong one."""
        text = (ROOT / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            bare = line.split("#")[0]
            assert "ical_url" not in bare and "calendar_id" not in bare, \
                f"{name}: private field set in a committed file: {line.strip()}"

    def test_the_secrets_file_is_ignored_by_git(self):
        """The whole arrangement rests on this one line in .gitignore."""
        try:
            done = subprocess.run(["git", "check-ignore", "-q", ".secrets.toml"],
                                  cwd=ROOT, capture_output=True)
        except (OSError, FileNotFoundError):
            pytest.skip("git not available")
        if done.returncode == 128:
            pytest.skip("not a git working copy")
        assert done.returncode == 0, ".secrets.toml is NOT git-ignored"

    @pytest.mark.parametrize("name", COMMITTED_SETTINGS)
    def test_committed_settings_parse(self, name):
        """They are the entry point for a fresh setup, and an example file has rotted
        before. If one stops being valid, this fails rather than the next first run."""
        import tomllib

        tomllib.loads((ROOT / name).read_text(encoding="utf-8"))
