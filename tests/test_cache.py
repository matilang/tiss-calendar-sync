"""The disk cache that makes the interface interactive.

What matters here is not that it stores things - it is that it does *not* fetch when it has
no reason to. An interface recomputes the diff on every click, and a cache that quietly
refetched would turn a university server into the thing absorbing the cost.
"""
from __future__ import annotations

import os
import time

import pytest

from tisscal.cache import Cache

FEED = b"BEGIN:VCALENDAR\nEND:VCALENDAR\n"
# Shaped like the real thing, unmistakably not it: a secret scan over this repo should
# not have to pause here.
URL = "https://tiss.tuwien.ac.at/feed?token=FAKE-VALUE-FOR-TESTS-ONLY"


def counting(result):
    calls = []

    def fetcher(*args):
        calls.append(args)
        return result

    return fetcher, calls


class TestFeed:
    def test_the_first_call_fetches(self, tmp_path):
        fetcher, calls = counting(FEED)
        assert Cache(tmp_path).feed(URL, fetcher=fetcher) == FEED
        assert len(calls) == 1

    def test_the_second_call_does_not(self, tmp_path):
        cache = Cache(tmp_path)
        fetcher, calls = counting(FEED)
        cache.feed(URL, fetcher=fetcher)
        assert cache.feed(URL, fetcher=fetcher) == FEED
        assert len(calls) == 1

    def test_a_stale_entry_is_refetched(self, tmp_path):
        cache = Cache(tmp_path)
        fetcher, calls = counting(FEED)
        cache.feed(URL, fetcher=fetcher)
        cache.feed(URL, max_age=0, fetcher=fetcher)
        assert len(calls) == 2

    def test_force_refetches_a_fresh_entry(self, tmp_path):
        cache = Cache(tmp_path)
        fetcher, calls = counting(FEED)
        cache.feed(URL, fetcher=fetcher)
        cache.feed(URL, force=True, fetcher=fetcher)
        assert len(calls) == 2

    def test_the_token_never_becomes_a_filename(self, tmp_path):
        """Filenames turn up in directory listings, backups and screenshots. The feed URL
        carries the TISS token, so the cache key is a hash of it."""
        fetcher, _ = counting(FEED)
        Cache(tmp_path).feed(URL, fetcher=fetcher)
        names = [p.name for p in (tmp_path / ".cache").iterdir()]
        assert names
        assert not any("FAKE-VALUE-FOR-TESTS-ONLY" in n for n in names)
        assert not any("token" in n for n in names)

    def test_two_feeds_do_not_share_an_entry(self, tmp_path):
        """TISS and TUWEL are both 'the feed' - one cache file for both would serve one
        profile the other's timetable."""
        cache = Cache(tmp_path)
        a, _ = counting(b"A")
        b, _ = counting(b"B")
        assert cache.feed("https://a.invalid/x", fetcher=a) == b"A"
        assert cache.feed("https://b.invalid/y", fetcher=b) == b"B"
        assert cache.feed("https://a.invalid/x", fetcher=a) == b"A"


class TestCoursePages:
    def test_a_page_is_cached_by_course_and_semester(self, tmp_path):
        cache = Cache(tmp_path)
        fetcher, calls = counting("<html>186814</html>")
        cache.course_html("186814", "2026W", fetcher=fetcher)
        cache.course_html("186814", "2026W", fetcher=fetcher)
        cache.course_html("186814", "2027S", fetcher=fetcher)
        assert len(calls) == 2

    def test_the_dot_in_a_course_number_is_normalised(self, tmp_path):
        """186.814 and 186814 are the same course, and must not be fetched twice."""
        cache = Cache(tmp_path)
        fetcher, calls = counting("<html/>")
        cache.course_html("186.814", "2026W", fetcher=fetcher)
        cache.course_html("186814", "2026W", fetcher=fetcher)
        assert len(calls) == 1


class TestTheScraperItHandsToThePipeline:
    def fake(self, course_html):
        """A cached scraper that needs no network: an injected page fetcher, and a session
        factory that counts how often a TISS handshake would have been opened."""
        fetcher, fetches = counting(course_html)
        sessions = []

        def session_factory():
            sessions.append(1)
            return "session"

        return fetcher, fetches, session_factory, sessions

    def test_it_parses_cached_html_without_fetching(self, tmp_path, course_html):
        cache = Cache(tmp_path)
        fetcher, fetches, factory, _ = self.fake(course_html)
        scrape = cache.scraper(fetcher=fetcher, session_factory=factory)

        first = scrape(["186814"], "2026W")
        second = scrape(["186814"], "2026W")

        assert len(fetches) == 1, "the second run should have come off the disk"
        assert first[0].course_nr == second[0].course_nr == "186814"
        assert first[0].exams, "the cached page should still parse into exams"

    def test_a_fully_cached_run_opens_no_session(self, tmp_path, course_html):
        """Opening a session is itself two requests to TISS, for the DeltaSpike handshake.
        A run that needs no page must not make them - the case while someone clicks."""
        cache = Cache(tmp_path)
        fetcher, _, factory, sessions = self.fake(course_html)
        cache.scraper(fetcher=fetcher, session_factory=factory)(["186814"], "2026W")
        assert sessions == [1], "the first run does need one"

        out = cache.scraper(fetcher=fetcher, session_factory=factory)(["186814"], "2026W")
        assert sessions == [1], "the second run opened a session it had no use for"
        assert out and out[0].exams

    def test_parsing_happens_every_time_so_parser_fixes_apply(self, tmp_path, course_html):
        """Why HTML is cached rather than parsed Course objects: a fix to the parser takes
        effect on the next run, with nobody having to know the cache exists."""
        cache = Cache(tmp_path)
        fetcher, _, factory, _ = self.fake(course_html)
        cache.scraper(fetcher=fetcher, session_factory=factory)(["186814"], "2026W")
        cached = list((tmp_path / ".cache").iterdir())
        assert len(cached) == 1
        assert cached[0].suffix == ".html"


class TestAdmin:
    def test_status_reports_an_age_per_file(self, tmp_path):
        cache = Cache(tmp_path)
        fetcher, _ = counting(FEED)
        cache.feed(URL, fetcher=fetcher)
        status = cache.status()
        assert len(status) == 1
        age = next(iter(status.values()))
        assert 0 <= age < 60

    def test_clear_empties_it(self, tmp_path):
        cache = Cache(tmp_path)
        fetcher, calls = counting(FEED)
        cache.feed(URL, fetcher=fetcher)
        assert cache.clear() == 1
        cache.feed(URL, fetcher=fetcher)
        assert len(calls) == 2

    def test_an_empty_cache_is_not_an_error(self, tmp_path):
        cache = Cache(tmp_path)
        assert cache.status() == {}
        assert cache.clear() == 0

    def test_age_of_something_never_fetched_is_none(self, tmp_path):
        assert Cache(tmp_path).age(tmp_path / "nope.ics") is None
