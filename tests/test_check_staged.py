"""The guard that refuses to commit a secret.

It exists because the suite's own check - that .secrets.toml is git-ignored - runs in CI,
which is after the push. For a public repository that is too late: public pushes are
scraped within minutes, so the cost of a slip is rotating three credentials rather than
amending a commit.

The hard part is not catching secrets. It is not crying wolf: .secrets.example.toml has to
show the shape of a feed URL, and the cache test needs something token-shaped to prove a
token never becomes a filename. Both are committed, and both must pass.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from check_staged import (SECRET_NAMES, offending_content,  # noqa: E402
                          offending_paths)

# Assembled at runtime, never written out whole, for two reasons.
#
# The first is circular and was found the hard way: a file full of literal secret-shaped
# strings is refused by the very guard it tests. It was, on the first attempt to commit it.
#
# The second is worse, and is why that mattered. The first draft reached for realistic
# values and used the opening characters of the *actual* TUWEL authtoken and the *actual*
# calendar id. The guard caught both, which is how they stayed out of the history.
# Fragments of a live credential have no business in a test, so these mean nothing.
HEX = "0123456789abcdef0123"
FEED_TOKEN = "token=" + HEX
TUWEL_TOKEN = "auth" + "token=" + HEX
PRIVATE_KEY = "-----BEGIN " + "PRIVATE KEY-----"
KEY_FIELD = '"private_key' + '_id": "' + HEX + '"'
CALENDAR_ID = HEX + "456789abcdef" + "@group" + ".calendar.google.com"


def diff(path: str, *added: str) -> str:
    """A staged diff with these lines added to one file."""
    nl = chr(10)
    head = (f"diff --git a/{path} b/{path}{nl}--- a/{path}{nl}"
            f"+++ b/{path}{nl}@@ -0,0 +1 @@{nl}")
    return head + "".join(f"+{line}{nl}" for line in added)


class TestByPath:
    def test_the_secrets_file_is_refused(self):
        assert offending_paths([".secrets.toml"]) == [".secrets.toml"]

    def test_the_service_account_key_is_refused(self):
        assert offending_paths(["service_account.json"])

    def test_a_copy_in_a_subdirectory_is_refused_too(self):
        """Matched by basename, because the mistake is the file, not the place."""
        assert offending_paths(["backup/old/.secrets.toml"])

    def test_the_pre_split_configs_are_refused(self):
        """They carried the feed token inline, which is why the split happened."""
        assert offending_paths(["config.toml", "tuwel.toml"]) == ["config.toml", "tuwel.toml"]

    def test_the_example_file_is_allowed(self):
        assert offending_paths([".secrets.example.toml"]) == []

    def test_ordinary_files_are_allowed(self):
        assert offending_paths(["settings.toml", "tisscal/web.py", "README.md"]) == []

    def test_every_listed_name_is_either_ignored_or_absent(self):
        """A name here that is actually tracked would mean the list is decorative."""
        import subprocess

        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                                 text=True, encoding="utf-8").stdout.split()
        assert not [n for n in tracked if Path(n).name in SECRET_NAMES]


class TestByContent:
    def test_a_feed_token_is_caught(self):
        hits = offending_content(diff("README.md",
                                      f"see https://tiss.tuwien.ac.at/f?{FEED_TOKEN}"))
        assert len(hits) == 1
        assert "feed token" in hits[0][0]
        assert hits[0][1] == "README.md"

    def test_a_tuwel_authtoken_is_caught(self):
        assert offending_content(diff("x.md", f"?userid=1&{TUWEL_TOKEN}"))

    def test_a_private_key_is_caught(self):
        assert offending_content(diff("key.json", PRIVATE_KEY))

    def test_a_service_account_field_is_caught(self):
        assert offending_content(diff("k.json", "  " + KEY_FIELD + ","))

    def test_a_calendar_id_is_caught(self):
        assert offending_content(diff("notes.md", CALENDAR_ID))

    def test_the_excerpt_does_not_print_the_whole_secret(self):
        """The message appears in a terminal and often in a screenshot after it."""
        hits = offending_content(diff("x.md", FEED_TOKEN + "tail-that-must-not-print"))
        assert "tail-that-must-not-print" not in hits[0][2]

    def test_a_removed_line_is_not_a_problem(self):
        """Content leaving a file is the fix, not the leak."""
        nl = chr(10)
        removal = (f"diff --git a/x b/x{nl}--- a/x{nl}+++ b/x{nl}"
                   f"@@ -1 +0,0 @@{nl}-{FEED_TOKEN}{nl}")
        assert offending_content(removal) == []

    def test_the_right_file_is_named_when_several_change(self):
        both = diff("clean.md", "nothing here") + diff("bad.md", FEED_TOKEN)
        hits = offending_content(both)
        assert [h[1] for h in hits] == ["bad.md"]


class TestItDoesNotCryWolf:
    """Every one of these is committed in this repository today and must stay committable."""

    def test_the_example_url_with_paste_placeholders(self):
        assert offending_content(diff(
            ".secrets.example.toml",
            'ical_url = "https://tuwel.tuwien.ac.at/x?userid=PASTE&authtoken=PASTE"')) == []

    def test_a_placeholder_long_enough_to_match_is_still_allowed(self):
        """The cache test needs a token-shaped value to prove a token never becomes a
        filename. It is long enough to match the pattern, and says FAKE."""
        assert offending_content(diff(
            "tests/test_cache.py",
            'URL = "https://tiss.tuwien.ac.at/feed?token=FAKE-VALUE-FOR-TESTS-ONLY"')) == []

    def test_the_placeholder_calendar_ids_in_the_tests(self):
        assert offending_content(diff("tests/conftest.py",
                                      '"calendar_id": "test@group.calendar.google.com"')) == []
        assert offending_content(diff(".secrets.example.toml",
                                      'calendar_id = "xxxxxxxx@group.calendar.google.com"')) == []

    def test_the_real_committed_files_pass(self):
        """The strongest version of the same point: run the guard over every tracked file as
        if it were newly added. A failure means either something private got committed or
        the guard is wrong - including about this very file."""
        import subprocess

        import pytest

        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                                 text=True, encoding="utf-8")
        if tracked.returncode != 0:
            pytest.skip("not a git working copy")

        as_added = []
        for name in tracked.stdout.split():
            path = ROOT / name
            # The saved TISS page and the iCal fixture are data, not source, and large.
            if not path.is_file() or path.suffix in (".ics", ".html"):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            as_added.append(diff(name, *text.splitlines()))
        assert offending_content("".join(as_added)) == []
        assert offending_paths(tracked.stdout.split()) == []
