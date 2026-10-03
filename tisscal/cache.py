"""Keeping the feed and the course pages on disk, so the interface can be interactive.

Every toggle in the interface re-runs the pipeline to work out what changed. The pipeline
needs the iCal feed and, for exams and registration windows, five TISS course pages. Fetching
those takes a few seconds and a dozen HTTP requests, which is fine once a day and absurd
once per click - so they are cached here.

Cached at the HTML level rather than as parsed objects, deliberately. Fetching is the
expensive part; parsing is microseconds and pure. Caching the raw pages means no dataclass
has to be serialised and deserialised, and a fix to the parser takes effect on the next run
instead of after someone remembers to clear the cache.

The cache key for the feed is a hash of its URL, never the URL itself: that URL carries the
TISS token, and filenames end up in directory listings, backups and screenshots.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

DIRNAME = ".cache"

# A timetable changes a few times a semester; a course page's exam dates even less often.
# Both are short enough that an interface session never shows yesterday's data, and long
# enough that clicking around costs nothing.
FEED_MAX_AGE = 30 * 60
COURSE_MAX_AGE = 6 * 3600


class Cache:
    """Fetched things, on disk, with an age."""

    def __init__(self, root: Path):
        self.dir = root / DIRNAME

    # ---------------------------------------------------------------- helpers
    def _file(self, name: str) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        return self.dir / name

    @staticmethod
    def age(path: Path) -> float | None:
        """Seconds since this was fetched, or None if it never was."""
        if not path.exists():
            return None
        return time.time() - path.stat().st_mtime

    def _fresh(self, path: Path, max_age: float, force: bool) -> bool:
        age = self.age(path)
        return not force and age is not None and age < max_age

    # ------------------------------------------------------------------ feed
    def feed(self, url: str, *, max_age: float = FEED_MAX_AGE, force: bool = False,
             fetcher=None) -> bytes:
        """The iCal feed, from disk if it was fetched recently enough."""
        # Hashed, so the token in the URL never becomes a filename.
        name = f"feed-{hashlib.sha1(url.encode()).hexdigest()[:12]}.ics"
        path = self._file(name)
        if self._fresh(path, max_age, force):
            return path.read_bytes()

        if fetcher is None:
            from .feed import fetch_feed as fetcher
        raw = fetcher(url)
        path.write_bytes(raw)
        return raw

    # --------------------------------------------------------- course pages
    def course_html(self, course_nr: str, semester: str, *,
                    max_age: float = COURSE_MAX_AGE, force: bool = False,
                    session=None, fetcher=None) -> str:
        """One TISS course page. `session` is only created if something must be fetched."""
        nr = str(course_nr).replace(".", "")
        path = self._file(f"course-{nr}-{semester}.html")
        if self._fresh(path, max_age, force):
            return path.read_text(encoding="utf-8")

        if fetcher is None:
            from .scrape import fetch_course as fetcher
        html = fetcher(nr, semester, session)
        path.write_bytes(html.encode("utf-8"))
        return html

    def scraper(self, *, max_age: float = COURSE_MAX_AGE, force: bool = False,
                fetcher=None, session_factory=None):
        """A scraper the pipeline can use, backed by this cache.

        Matches the shape the pipeline expects - scraper(numbers, semester) -> [Course] -
        so it drops straight into build_events and plan.compare.

        The TISS session is created lazily, and only if some page actually has to be
        fetched: opening one is itself two requests, for the DeltaSpike window handshake.
        A run where every page is cached therefore touches the network not at all, which is
        the common case while someone is clicking around an interface and the whole reason
        this module exists.
        """
        def scrape(numbers, semester):
            from .scrape import parse_course

            factory = session_factory
            if factory is None:
                from .scrape import make_session as factory

            session = None
            out = []
            for nr in numbers:
                path = self._file(f"course-{str(nr).replace('.', '')}-{semester}.html")
                if session is None and not self._fresh(path, max_age, force):
                    session = factory()
                html = self.course_html(nr, semester, max_age=max_age, force=force,
                                        session=session, fetcher=fetcher)
                out.append(parse_course(html, str(nr), semester))
            return out

        return scrape

    # ----------------------------------------------------------------- admin
    def status(self) -> dict[str, float | None]:
        """{filename: age in seconds} for everything cached, for display."""
        if not self.dir.exists():
            return {}
        return {p.name: self.age(p) for p in sorted(self.dir.iterdir()) if p.is_file()}

    def clear(self) -> int:
        """Throw everything away. Returns how many files went."""
        if not self.dir.exists():
            return 0
        gone = 0
        for p in self.dir.iterdir():
            if p.is_file():
                p.unlink()
                gone += 1
        return gone
