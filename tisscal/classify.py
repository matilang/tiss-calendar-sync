"""Deciding what kind of thing an event is.

Both answers are keyword-based, because the TISS feed says what a slot is only in
its description ("Lecture", "Exercise sessions", "Algorithmics Q&A") and a TUWEL
deadline says it only in its title.
"""
from __future__ import annotations

from .model import Lecture


def is_exercise(ev: Lecture, cfg: dict) -> bool:
    """Does this event look like an exercise slot rather than a lecture?

    Matched on the description, which is where TISS says what a slot is ("Lecture",
    "Exercise sessions", "Algorithmics Exercises 4"). Deliberately does not match the
    Q&A sessions - those are not exercises and should keep the lecture treatment.
    """
    words = cfg.get("exercises", {}).get("keywords", ["Exercise", "Übung", "Uebung"])
    hay = f"{ev.summary} {ev.description}".lower()
    return any(w.lower() in hay for w in words)


def is_exam(ev: Lecture, cfg: dict) -> bool:
    kws = cfg["reminders"].get("exam_keywords",
                               ["prüfung", "exam", "test", "klausur", "abgabe", "deadline"])
    text = f"{ev.summary} {ev.description}".lower()
    return any(k.lower() in text for k in kws)
