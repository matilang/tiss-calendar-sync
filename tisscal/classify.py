"""Deciding what kind of thing an event is.

Both answers are keyword-based, because the TISS feed says what a slot is only in
its description ("Lecture", "Exercise sessions", "Algorithmics Q&A") and a TUWEL
deadline says it only in its title.

Each function takes the one config section it reads, not the whole config, so what it
depends on is visible from the signature.
"""
from __future__ import annotations

from .config import Exercises, Reminders
from .model import Lecture


def is_exercise(ev: Lecture, exercises: Exercises) -> bool:
    """Does this event look like an exercise slot rather than a lecture?

    Matched on the description, which is where TISS says what a slot is ("Lecture",
    "Exercise sessions", "Algorithmics Exercises 4"). Deliberately does not match the
    Q&A sessions - those are not exercises and should keep the lecture treatment.
    """
    hay = f"{ev.summary} {ev.description}".lower()
    return any(w.lower() in hay for w in exercises.keywords)


def is_exam(ev: Lecture, reminders: Reminders) -> bool:
    """Exam-ish by keyword. The only thing available for a TUWEL deadline, which arrives
    as an ordinary feed event with no kind of its own."""
    text = f"{ev.summary} {ev.description}".lower()
    return any(k.lower() in text for k in reminders.exam_keywords)
