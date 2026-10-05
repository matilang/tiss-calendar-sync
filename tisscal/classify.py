"""Deciding what kind of thing an event is.

Both answers are keyword-based, because the TISS feed says what a slot is only in
its description ("Lecture", "Exercise sessions", "Algorithmics Q&A") and a TUWEL
deadline says it only in its title.

Each function takes the one config section it reads, not the whole config, so what it
depends on is visible from the signature.
"""
from __future__ import annotations

import re

from .config import Exercises, Reminders
from .model import Lecture

# Once you are registered for an exercise group, TISS stops describing the slot and starts
# naming it after the group and the hour instead:
#
#     194.187 Advanced Software Engineering 3_11:00-12:00
#                                           ^ group 3, the 11:00 slot
#
# The word "exercise" appears nowhere in that, which is exactly why keywords alone let a
# registered exercise through as a lecture - it reached the calendar as "VU ASE" among the
# lectures, with nothing to tell them apart. The 10-hour "Exercise sessions" blocks TISS
# publishes *before* registration do say so, and are matched by keyword as before.
GROUP_SLOT = re.compile(r"\b\d+_\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}")


def is_exercise(ev: Lecture, exercises: Exercises) -> bool:
    """Does this event look like an exercise slot rather than a lecture?

    Matched on the description, which is where TISS says what a slot is ("Lecture",
    "Exercise sessions", "Algorithmics Exercises 4"). Deliberately does not match the
    Q&A sessions - those are not exercises and should keep the lecture treatment.
    """
    hay = f"{ev.summary} {ev.description}".lower()
    if any(w.lower() in hay for w in exercises.keywords):
        return True
    # The description only: the summary is the course title, which every event of the
    # course carries, so matching there would make the whole course look like exercises.
    return bool(GROUP_SLOT.search(ev.description or ""))


def is_exam(ev: Lecture, reminders: Reminders) -> bool:
    """Exam-ish by keyword. The only thing available for a TUWEL deadline, which arrives
    as an ordinary feed event with no kind of its own."""
    text = f"{ev.summary} {ev.description}".lower()
    return any(k.lower() in text for k in reminders.exam_keywords)
