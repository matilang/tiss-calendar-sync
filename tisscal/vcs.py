"""The little bit of git the interface needs to tell the truth.

The daily sync runs from the settings **as committed**, so a saved file is not yet a changed
calendar. An interface that let you toggle a course, showed you the consequence and said
nothing else would be quietly misleading: the change would not reach the cloud until it was
committed and pushed. So the page shows where the file stands, and offers the one command
that closes the gap.

Everything here is read-only except `commit_push`, which only ever runs when someone presses
the button for it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(root: Path, *args: str) -> tuple[int, str, str]:
    """Run git here. Trailing whitespace goes; leading whitespace is left alone.

    That distinction is not pedantry. `git status --porcelain` puts the two status
    characters in columns 1-2, so an unstaged change reads " M settings.toml" with a
    leading space - and stripping it shifted every name one character left. The interface
    then looked for "settings.toml" in a list containing "ettings.toml", decided the file
    was committed, and told you the cloud was running what you were looking at while it was
    not. One space, and the page said the opposite of the truth.
    """
    done = subprocess.run(("git",) + args, cwd=root, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    return done.returncode, (done.stdout or "").rstrip(), (done.stderr or "").strip()


def available(root: Path) -> bool:
    try:
        return _git(root, "rev-parse", "--git-dir")[0] == 0
    except OSError:
        return False        # git not installed at all


def status(root: Path, *paths: str) -> dict:
    """Where the working copy stands, as far as the interface cares.

    `ahead` is how many commits are not on the remote yet, which is the number that decides
    whether the cloud is running what you are looking at.
    """
    if not available(root):
        return {"available": False}

    _, branch, _ = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    _, porcelain, _ = _git(root, "status", "--porcelain", "--", *paths) if paths else \
        _git(root, "status", "--porcelain")
    changed = [l[3:] for l in porcelain.splitlines() if l.strip()]

    ahead = 0
    code, counts, _ = _git(root, "rev-list", "--count", "@{upstream}..HEAD")
    if code == 0 and counts.isdigit():
        ahead = int(counts)
    has_upstream = code == 0

    _, last, _ = _git(root, "log", "-1", "--format=%h %s")

    return {"available": True, "branch": branch, "changed": changed,
            "dirty": bool(changed), "ahead": ahead, "has_upstream": has_upstream,
            "last_commit": last}


def commit_push(root: Path, paths: list[str], message: str, *, push: bool = True) -> dict:
    """Commit the named paths and, by default, push.

    Only the named paths are staged. A blanket `git add -A` from a web page would be a
    good way to commit a secret that happened to be lying around untracked.
    """
    if not message.strip():
        return {"ok": False, "output": "a commit message is required"}

    steps = [("add", ("add", "--", *paths)),
             ("commit", ("commit", "-m", message))]
    if push:
        steps.append(("push", ("push",)))

    log = []
    for name, args in steps:
        code, out, err = _git(root, *args)
        log.append(f"$ git {' '.join(args)}\n{out or err}".strip())
        if code != 0:
            return {"ok": False, "failed_at": name, "output": "\n\n".join(log)}
    return {"ok": True, "output": "\n\n".join(log)}


def file_at(root: Path, name: str, ref: str) -> str | None:
    """A tracked file's contents at some revision, or None if it is not there.

    Shared by the interface and `preview --diff`, both of which compare against the
    committed settings rather than the working copy, because that is what the daily sync
    actually runs.
    """
    code, out, _ = _git(root, "show", f"{ref}:{name}")
    return out + "\n" if code == 0 else None
