"""What an event is called on the calendar."""
from __future__ import annotations

from typing import Mapping

from .classify import is_exercise
from .config import Exercises
from .model import TYPE_CODES, Lecture


def course_label(course_nr: str | None, titles: Mapping[str, str]) -> str:
    """Short name configured for a course in [titles], or "" if none is set."""
    return titles.get(course_nr or "", "")


def display_title(ev: Lecture, titles: Mapping[str, str], exercises: Exercises) -> str:
    """What the event is called on the calendar.

    Two different shapes come in. TISS titles already name the course and lead with its
    number ("186.814 VU Algorithmics"), which is just noise once you know your own
    courses - so the number comes off, and a [titles] entry can shorten the rest
    ("VU Management of Graph Data" -> "VU MoGD"). TUWEL titles instead say only what is
    due ("Project Report ist fällig") and never mention the course, so the short name
    goes in front; without it the deadline would be unattributable.
    """
    label = course_label(ev.course_nr, titles)
    title = ev.summary

    if ev.course_nr and ev.course_nr in title:
        title = label or title.replace(ev.course_nr, "", 1).strip(" -–:")
    elif label and label in title:
        # Exam and registration events are built with the label already in them
        # ("EXAM VU ASE Test 1") - prefixing again would repeat it.
        pass
    elif ev.course_nr:
        title = f"{label or ev.course_nr} {title}".strip()

    return _exercise_code(title, ev, exercises)


def _exercise_code(title: str, ev: Lecture, exercises: Exercises) -> str:
    """Swap the leading course-type code on exercise slots: "VU Algorithmics" -> "UE ...".

    The course type is VU (lecture plus exercise), but an individual exercise slot is an
    Übung, and telling the two apart at a glance in the calendar is the whole point.
    Only applies to feed events - an exam or a registration reminder keeps the course's
    own code.
    """
    if not exercises.type_code or ev.kind != "lecture" or not is_exercise(ev, exercises):
        return title
    head, _, rest = title.partition(" ")
    if head in TYPE_CODES and rest:
        return f"{exercises.type_code} {rest}"
    return title
