"""The event type everything else passes around, plus the constants that define it."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo


VIENNA = ZoneInfo("Europe/Vienna")


COURSE_NR = re.compile(r"\b(\d{3}\.[0-9A-Z]{3})\b")  # e.g. 185.A91, 104.265




# TISS stamps each UID with the time the feed was generated, e.g.
# "20261107T103712Z-5491884@tiss.tuwien.ac.at" - the same lecture comes back
# under a new UID on every request. Only the part after the timestamp identifies it.
UID_TIMESTAMP = re.compile(r"^\d{8}T\d{6}Z-")


# TISS course-type codes that can lead a title. An exercise slot of a VU is still an
# Übung, so the code is swapped rather than the course renamed.
TYPE_CODES = ("VU", "VO", "UE", "PR", "SE", "LU", "PV", "AG", "EX")


# --------------------------------------------------------------------------- #
# Feed parsing
# --------------------------------------------------------------------------- #
@dataclass
class Lecture:
    uid: str
    summary: str
    start: datetime | date
    end: datetime | date
    location: str
    description: str
    course_nr: str | None
    # "lecture" from the iCal feed, or "exam"/"registration" built by tisscal.events.
    # Decides which reminder set and colour the event gets.
    kind: str = "lecture"
    # iCal CATEGORIES. TUWEL puts the course and its semester here ("192.161-2026W")
    # while the title says only "Project Report ist fällig" - it is the only way to tell
    # which course a deadline belongs to, and to spot last year's leftovers.
    categories: str = ""
    # For kind == "registration": what you are registering for - "exam", "course" or
    # "group". Exam sign-up wants a warning days ahead; an exercise group is
    # first-come-first-served, so there the only useful moment is when it opens.
    scope: str = ""

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, datetime)

    @property
    def start_dt(self) -> datetime:
        if isinstance(self.start, datetime):
            return self.start
        return datetime.combine(self.start, time.min, VIENNA)

    @property
    def stable_uid(self) -> str:
        """UID without the feed-generation timestamp TISS prefixes onto it."""
        return UID_TIMESTAMP.sub("", self.uid)

    @property
    def gcal_id(self) -> str:
        # Google event ids allow [a-v0-9]; hex digest fits and is stable per event.
        # Hash the timestamp-stripped UID only: TISS issues one UID per occurrence, so the
        # start time adds no uniqueness, and leaving it out lets a rescheduled lecture be
        # updated in place instead of deleted and recreated.
        return hashlib.sha1(self.stable_uid.encode()).hexdigest()
