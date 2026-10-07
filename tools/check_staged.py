#!/usr/bin/env python3
"""Refuse to commit anything that must never leave this machine.

The test suite already checks that .secrets.toml is git-ignored, but that runs in CI -
after the push. On a private repository the difference hardly matters: you notice, you fix
it. On a public one it is the difference between an awkward moment and rotating the TISS
token, the TUWEL authtoken and the Google service account key, because public pushes are
scraped within minutes.

So this runs before the commit exists. Two checks, because either alone has a hole:

    by path     the files that are private by nature, however they got staged - `git add -f`
                defeats .gitignore and is exactly how an accident happens
    by content  the shape of a real secret anywhere in the staged diff, which catches the
                case nobody plans for: a token pasted into a comment, a README, a test

Install it once per clone:

    git config core.hooksPath tools/githooks

It is a guard, not a wall: `git commit --no-verify` goes straight past it, and that is
deliberate - a check you cannot override gets worked around in worse ways.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Private by nature. Listed by basename, so a copy in a subdirectory is caught too.
SECRET_NAMES = (
    ".secrets.toml", "service_account.json", "credentials.json", "token.json",
    # Pre-split single-file configs. They carried the feed token inline.
    "config.toml", "tuwel.toml",
    # The pre-cleanup dump of every deleted event: a full personal timetable.
    "deleted_untagged_backup.json", "cloud_test_baseline.json",
)

PATTERNS = (
    ("a TISS or TUWEL feed token",
     re.compile(r"(?:auth)?token=([A-Za-z0-9_-]{12,})")),
    ("a private key",
     re.compile(r"BEGIN (?:RSA |EC )?PRIVATE KEY")),
    ("a service account key field",
     re.compile(r'"private_key(?:_id)?"\s*:\s*"[^"]{12,}')),
    ("a Google calendar id",
     re.compile(r"\b[a-z0-9]{24,}@group\.calendar\.google\.com")),
)

# Deliberately fake values that have to stay greppable: the example file shows the shape of
# a URL, and the tests need something token-shaped to prove it never becomes a filename.
PLACEHOLDERS = ("PASTE", "FAKE", "not-a-real", "xxxx", "YOUR", "example", "invalid")


def looks_like_a_placeholder(hit: str) -> bool:
    return any(p.lower() in hit.lower() for p in PLACEHOLDERS)


def offending_paths(paths: list[str]) -> list[str]:
    return [p for p in paths if Path(p).name in SECRET_NAMES]


def offending_content(diff: str) -> list[tuple[str, str, str]]:
    """(reason, file, excerpt) for every added line that looks like a real secret.

    Only added lines - a `-` line is content leaving the file, which is the fix, not the
    problem.
    """
    found: list[tuple[str, str, str]] = []
    current = "?"
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for reason, pattern in PATTERNS:
            match = pattern.search(line)
            if match and not looks_like_a_placeholder(match.group(0)):
                excerpt = match.group(0)
                found.append((reason, current, excerpt[:18] + "..."))
    return found


def _git(*args: str) -> str:
    done = subprocess.run(("git",) + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return done.stdout or ""


def main() -> int:
    paths = [p for p in _git("diff", "--cached", "--name-only").splitlines() if p.strip()]
    if not paths:
        return 0

    problems: list[str] = []
    for path in offending_paths(paths):
        problems.append(f"  {path}\n      is private by nature and must never be committed")
    for reason, path, excerpt in offending_content(_git("diff", "--cached")):
        problems.append(f"  {path}\n      contains what looks like {reason}: {excerpt}")

    if not problems:
        return 0

    print("Refusing to commit - this would publish a secret:\n", file=sys.stderr)
    print("\n".join(problems), file=sys.stderr)
    print("\nUnstage it with `git restore --staged <file>`. If this is a false alarm,\n"
          "`git commit --no-verify` goes past this check.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
