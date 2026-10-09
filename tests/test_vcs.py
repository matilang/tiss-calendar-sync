"""The only part of this project that can change something outside this machine.

The interface has a button that commits and pushes, because a saved settings file is not yet
a changed calendar - the daily run uses what is committed. That button had no tests at all,
which is a poor place for the gap to be: everything else here either reads, or writes one
file that can be inspected afterwards.

Everything below runs in a throwaway repository with a local bare remote, so a push is a
real push and reaches nothing.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from tisscal import vcs


def git(cwd, *args):
    done = subprocess.run(("git",) + args, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8")
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout.strip()


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """One repository with a bare remote, built once.

    Building it per test meant eight git processes before the test began, and on Windows
    that turned this file into four fifths of the suite's running time. Copying a finished
    one is two directory copies.
    """
    base = tmp_path_factory.mktemp("template")
    remote, work = base / "remote.git", base / "work"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                   capture_output=True, check=True)
    work.mkdir()
    git(work, "init", "-b", "main")
    git(work, "config", "user.email", "test@example.invalid")
    git(work, "config", "user.name", "Test")
    # Not the real repository's hooks: this one must be able to commit freely.
    git(work, "config", "core.hooksPath", str(base / "no-hooks"))

    (work / "settings.toml").write_text('courses = ["186.814"]\n', encoding="utf-8")
    git(work, "add", "settings.toml")
    git(work, "commit", "-m", "first")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-u", "origin", "main")
    return base


@pytest.fixture
def repo(template, tmp_path):
    """A private copy of it, with its own remote, so pushes cannot leak between tests."""
    shutil.copytree(template / "remote.git", tmp_path / "remote.git")
    shutil.copytree(template / "work", tmp_path / "work")
    work = tmp_path / "work"
    git(work, "remote", "set-url", "origin", str(tmp_path / "remote.git"))
    return work


class TestStatus:
    def test_a_clean_repository(self, repo):
        state = vcs.status(repo)
        assert state["available"] is True
        assert state["branch"] == "main"
        assert state["dirty"] is False
        assert state["ahead"] == 0
        assert state["last_commit"].endswith("first")

    def test_a_modified_file_shows_up(self, repo):
        (repo / "settings.toml").write_text('courses = []\n', encoding="utf-8")
        state = vcs.status(repo)
        assert state["dirty"] is True
        assert "settings.toml" in state["changed"]

    def test_an_unpushed_commit_counts_as_ahead(self, repo):
        """The number the interface turns into "the cloud is running something else"."""
        (repo / "settings.toml").write_text('courses = ["186.814", "194.187"]\n',
                                            encoding="utf-8")
        git(repo, "commit", "-am", "second")
        assert vcs.status(repo)["ahead"] == 1

    def test_somewhere_that_is_not_a_repository(self, tmp_path):
        assert vcs.status(tmp_path / "nowhere") == {"available": False}
        assert vcs.available(tmp_path / "nowhere") is False


class TestCommitAndPush:
    def test_it_commits_and_pushes(self, repo):
        (repo / "settings.toml").write_text('courses = ["192.216"]\n', encoding="utf-8")
        result = vcs.commit_push(repo, ["settings.toml"], "Add a course")

        assert result["ok"] is True
        assert vcs.status(repo)["dirty"] is False
        assert vcs.status(repo)["ahead"] == 0
        assert "Add a course" in git(repo, "log", "-1", "--format=%s")

    def test_it_stages_only_what_it_was_given(self, repo):
        """The property the whole design rests on. A blanket `git add -A` from a web page
        would commit whatever else happened to be lying around - and what tends to be lying
        around in this project is a file with a token in it."""
        (repo / "settings.toml").write_text('courses = ["192.216"]\n', encoding="utf-8")
        (repo / ".secrets.toml").write_text('ical_url = "https://x/?token=sensitive"\n',
                                            encoding="utf-8")

        vcs.commit_push(repo, ["settings.toml"], "Add a course", push=False)

        committed = git(repo, "show", "--name-only", "--format=", "HEAD").split()
        assert committed == ["settings.toml"]
        assert ".secrets.toml" in vcs.status(repo)["changed"], "still untracked, as it must be"

    def test_push_can_be_left_out(self, repo):
        (repo / "settings.toml").write_text('courses = []\n', encoding="utf-8")
        assert vcs.commit_push(repo, ["settings.toml"], "Local only", push=False)["ok"]
        assert vcs.status(repo)["ahead"] == 1

    def test_an_empty_message_is_refused(self, repo):
        (repo / "settings.toml").write_text('courses = []\n', encoding="utf-8")
        result = vcs.commit_push(repo, ["settings.toml"], "   ")
        assert result["ok"] is False
        assert "message" in result["output"]
        assert vcs.status(repo)["dirty"] is True, "and nothing was committed"

    def test_nothing_to_commit_fails_rather_than_pretending(self, repo):
        result = vcs.commit_push(repo, ["settings.toml"], "Nothing changed", push=False)
        assert result["ok"] is False
        assert result["failed_at"] == "commit"

    def test_a_failure_says_which_step_and_shows_the_output(self, repo):
        """The page prints this log verbatim; a failure that said only "it failed" would
        leave someone guessing between a commit problem and a push problem."""
        git(repo, "remote", "set-url", "origin", str(repo / "nonexistent.git"))
        (repo / "settings.toml").write_text('courses = []\n', encoding="utf-8")

        result = vcs.commit_push(repo, ["settings.toml"], "Will not push")
        assert result["ok"] is False
        assert result["failed_at"] == "push"
        assert "git commit" in result["output"], "the step that did work is shown too"
        assert vcs.status(repo)["ahead"] == 1, "the commit stands; only the push failed"


class TestFileAt:
    def test_it_reads_a_file_at_a_revision(self, repo):
        (repo / "settings.toml").write_text('courses = ["changed"]\n', encoding="utf-8")
        git(repo, "commit", "-am", "second")

        assert "186.814" in vcs.file_at(repo, "settings.toml", "HEAD~1")
        assert "changed" in vcs.file_at(repo, "settings.toml", "HEAD")

    def test_a_file_that_is_not_there_is_none(self, repo):
        assert vcs.file_at(repo, "settings.tuwel.toml", "HEAD") is None

    def test_a_revision_that_does_not_exist_is_none(self, repo):
        """`preview --diff` turns this into a readable message instead of a traceback."""
        assert vcs.file_at(repo, "settings.toml", "no-such-ref") is None
