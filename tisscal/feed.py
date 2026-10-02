"""Fetching and parsing an iCal feed - TISS, TUWEL, or a local .ics file."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import requests
from icalendar import Calendar

from .model import COURSE_NR, VIENNA, Lecture

# Use the OS certificate store instead of certifi's bundle. Needed when a local
# antivirus / corporate proxy re-signs HTTPS traffic (its root is in the Windows
# store but not in certifi), which otherwise fails with CERTIFICATE_VERIFY_FAILED.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass


def _to_aware(v):
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=VIENNA)
    return v


def fetch_feed(url_or_path: str) -> bytes:
    if url_or_path.startswith(("http://", "https://")):
        r = requests.get(url_or_path, timeout=30)
        r.raise_for_status()
        return r.content
    return Path(url_or_path).read_bytes()


def parse_feed(raw: bytes) -> list[Lecture]:
    cal = Calendar.from_ical(raw)
    out: list[Lecture] = []
    for comp in cal.walk("VEVENT"):
        summary = str(comp.get("SUMMARY", "")).strip()
        desc = str(comp.get("DESCRIPTION", "")).strip()
        start = _to_aware(comp.decoded("DTSTART"))
        end = _to_aware(comp.decoded("DTEND")) if comp.get("DTEND") else start
        cats = comp.get("CATEGORIES")
        if cats is not None:
            cats = ",".join(str(c) for c in getattr(cats, "cats", [])) or str(cats)
        cats = (cats or "").strip()
        m = COURSE_NR.search(summary) or COURSE_NR.search(desc) or COURSE_NR.search(cats)
        out.append(Lecture(
            uid=str(comp.get("UID", summary)),
            summary=summary,
            start=start,
            end=end,
            location=str(comp.get("LOCATION", "")).strip(),
            description=desc,
            course_nr=m.group(1) if m else None,
            categories=cats,
        ))
    return sorted(out, key=lambda e: e.start_dt)
