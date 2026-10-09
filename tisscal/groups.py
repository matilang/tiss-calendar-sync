"""Which exercise group an event belongs to, whatever TISS decided to call them.

TISS has two unrelated ways of modelling exercise groups, and which one a course uses
decides whether any of this is needed at all.

    194.187 ASE    groups are named "1_09:00-10:00" .. "16_...", the appointments hang off
                   the group, and the feed contains *only the group you registered for* -
                   one token, four events. Nothing to filter.

    192.216 EoAI   groups are named "Exercises Group A (Labs)", "B", "C [TEMPORARY]", the
                   appointments hang off the *course*, and the feed contains all three
                   whether you registered or not - A: 39 events, B: 14, C: 15. Two thirds
                   of them are somebody else's exercises.

That difference is in TISS's data model, not in its wording, so no amount of reading the
course page harder fixes the second case. The group has to be stated once, in [groups].

What is worth automating is the *reading*, because the naming is arbitrary: "Group B",
"Gruppe 2", "Gr. 3", "Exercise Group A1", "4_12:00-13:00" are all the same idea, and next
semester it will be a fourth spelling. So both sides - the event's description and the
value written in the settings file - go through the same extractor and come out as one
normalised token. "B", "b", "Group B", "Exercises Group B" and "gruppe b" all mean B, which
means the setting can be whatever you copied out of TISS and it still matches.

Nothing here decides anything; filters.keep_chosen_groups does. This only reads.
"""
from __future__ import annotations

import re

# The labels a group marker can hide behind, longest first so "gruppe" is not cut short to
# "gr" with "uppe" left over. German and English, since the feed mixes them freely.
LABELS = (
    "uebungsgruppen", "übungsgruppen", "ubungsgruppen",
    "uebungsgruppe", "übungsgruppe", "ubungsgruppe",
    "exercise group", "exercise groups", "gruppen", "gruppe", "groups", "group",
    "kohorte", "cohort", "grp", "gr",
)

# One or two characters, and a letter-only token must be exactly one. That is what keeps
# "Group of students" and "Management of Graph Data" from parsing as groups "of" and "a":
# a bare word cannot be a token, so the only way to be wrong is a real "Group X" that
# means something else.
TOKEN = r"([A-Za-z]\d{0,2}|\d{1,2}[A-Za-z]?)"

# "Group B", "Gruppe 2", "Gr. 3", "group: A1". The separator is optional because TISS
# writes both "Group A" and "Gruppe2", and the lookahead is what refuses "Group of".
NAMED = re.compile(r"\b(?:" + "|".join(LABELS) + r")\b\.?\s*:?\s*" + TOKEN + r"(?![\w-])",
                   re.IGNORECASE)

# The other shape entirely: a registered ASE slot is called "3_11:00-12:00" and the word
# "group" appears nowhere in it. The leading number is the group.
SLOT = re.compile(r"\b(\d{1,2})_\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}")


def normalise(token: str) -> str:
    """One spelling per group, so the file and the feed can be compared at all.

    Upper-cased, and a leading zero dropped: TISS writes "Gruppe 2" on the course page and
    "Gruppe 02" in a description often enough that treating those as two groups would
    quietly filter out every event of the group you picked.
    """
    text = token.strip().upper()
    digits = re.fullmatch(r"(\d+)([A-Z]?)", text)
    return f"{int(digits.group(1))}{digits.group(2)}" if digits else text


def group_of(text: str) -> str:
    """The group token in this text, or "" if it names none.

    Read off the description rather than the title: the title is the course name, carried
    by every event of the course, so a group mentioned there would tag the lectures too.
    """
    if not text:
        return ""
    found = NAMED.search(text) or SLOT.search(text)
    return normalise(found.group(1)) if found else ""


def wanted(value: str) -> str:
    """The token a [groups] entry means, however it was written.

    Deliberately the same two steps as `group_of`, then the raw value as a fallback, so
    "B", "b", "Group B", "Exercises Group B" and "Gruppe B" are one answer and a plain "B"
    does not need the word "group" in front of it to be understood.
    """
    return group_of(value) or normalise(value or "")


def found_in(descriptions: list[str]) -> dict[str, str]:
    """{token: the full text it was first seen in}, for the groups present in a feed.

    The interface turns this into the list you choose from, which is why the original text
    comes back with the token: "A" is not a thing anyone recognises, "Exercises Group A
    (Labs)" is. Taken from the feed rather than from the course page on purpose - a group
    with no appointments has nothing to filter, so offering it would only be a way to pick
    a setting that silently empties the course.
    """
    out: dict[str, str] = {}
    for text in descriptions:
        token = group_of(text)
        if token and token not in out:
            out[token] = text
    return out
