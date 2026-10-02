#!/usr/bin/env python3
"""
tiss_scrape.py - read a TISS course page for the things the iCal feed does not carry:
exam dates, exam registration windows, course/group registration deadlines, and the
dates of courses you are not registered for yet.

Why this exists
---------------
The personal iCal feed only contains courses you are already registered for, and it
contains no exams at all - every event in it is a lecture or exercise slot. The course
page has all of it, but fetching it is not quite a plain GET:

TISS renders course pages with window-scoped JSF (Apache DeltaSpike). A first request
returns only a "Loading..." shell whose JavaScript generates a window id, stores it in a
cookie named `dsrwid-<token>`, and reloads the page with `?dsrid=<token>`. Replaying that
handshake with two lines of cookie setup returns the fully rendered page - no headless
browser needed.

Commands
--------
  python tiss_scrape.py 186814 194187        # report dates, exams and deadlines
  python tiss_scrape.py 186814 --json        # same data as JSON
  python tiss_scrape.py --config config.toml # take the course list from [scrape].courses
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Same reason as in tiss_sync.py: a local antivirus re-signs HTTPS here, so Python has
# to verify against the OS certificate store rather than certifi's bundle.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

try:  # keep umlauts readable on a cp1252 console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

BASE = "https://tiss.tuwien.ac.at"
COURSE_URL = BASE + "/course/courseDetails.xhtml"
DATE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
TIME = re.compile(r"\b(\d{2}):(\d{2})\b")

# Tables are identified by their header row, not by position or id: TISS ids are
# generated (j_id_2x:eventTable) and shift between pages and releases.
SINGLE_DATES = ("Day", "Date", "Time", "Location", "Description")
COURSE_DATES = ("Day", "Time", "Date", "Location", "Description")
EXAMS = ("Day", "Time", "Date", "Room", "Mode of examination",
         "Application time", "Application mode", "Exam")
GROUP_DATES = ("Group", "Day", "Time", "Date", "Location", "Description")
COURSE_REG = ("Begin", "End", "Deregistration end")
GROUP_REG = ("Group", "Registration From", "To")

# PrimeFaces DataTables are paginated server-side: the HTML carries only the first page
# (20 rows), but the widget's inline config states the real total. Parsing that lets us
# say "20 of 27" instead of silently dropping the rest. Driving the pagination itself is
# not possible - the AJAX POST needs window state we cannot reproduce and answers 500.
PAGINATOR = re.compile(
    r'id:"(?P<id>[^"]+)",paginator:\{[^}]*?rows:(?P<rows>\d+),rowCount:(?P<total>\d+)')


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def make_session() -> requests.Session:
    """Session that has completed the DeltaSpike window handshake."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    s.get(BASE + "/", timeout=30).raise_for_status()   # picks up _tiss_session
    token = str(random.randrange(100, 999))
    s.cookies.set("dsrwid-" + token, str(random.randrange(1000, 9999)),
                  domain="tiss.tuwien.ac.at", path="/")
    s.params = {"dsrid": token}                        # type: ignore[assignment]
    return s


def fetch_course(course_nr: str, semester: str, sess: requests.Session) -> str:
    nr = course_nr.replace(".", "")
    r = sess.get(COURSE_URL, params={"semester": semester, "courseNr": nr, "locale": "en"},
                 timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"       # TISS serves UTF-8; requests guesses latin-1 from the header
    if "Loading..." in r.text and len(r.text) < 12000:
        raise RuntimeError(f"{course_nr}: got the JS shell back, the dsrid handshake failed")
    return r.text


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
@dataclass
class Occurrence:
    """One concrete lecture/exercise slot."""
    date: str                 # ISO
    start: str                # "HH:MM"
    end: str
    location: str
    description: str
    group: str = ""


@dataclass
class Exam:
    title: str
    date: str                 # ISO
    start: str
    end: str
    room: str
    mode: str                 # written / oral
    apply_from: str = ""      # ISO datetime
    apply_to: str = ""
    apply_via: str = ""


@dataclass
class Course:
    course_nr: str
    semester: str
    title: str = ""
    occurrences: list[Occurrence] = field(default_factory=list)
    exams: list[Exam] = field(default_factory=list)
    registration: dict[str, str] = field(default_factory=dict)
    group_registration: list[dict[str, str]] = field(default_factory=list)
    # Tables TISS paginated away on us: {table id: (rows we got, rows that exist)}.
    partial: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.partial


def _iso_date(text: str, nth: int = 0) -> str:
    hits = DATE.findall(text)
    if len(hits) <= nth:
        return ""
    d, m, y = hits[nth]
    return f"{y}-{m}-{d}"


def _times(text: str) -> tuple[str, str]:
    hits = TIME.findall(text)
    if not hits:
        return "", ""
    first = f"{hits[0][0]}:{hits[0][1]}"
    last = f"{hits[-1][0]}:{hits[-1][1]}" if len(hits) > 1 else first
    return first, last


def _iso_dt(text: str, nth: int = 0) -> str:
    """'02.11.2026 00:00 - 25.11.2026 23:59' -> ISO datetime of the nth timestamp."""
    parts = re.findall(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})", text)
    if len(parts) <= nth:
        d = _iso_date(text, nth)
        return f"{d}T00:00" if d else ""
    d, m, y, hh, mm = parts[nth]
    return f"{y}-{m}-{d}T{hh}:{mm}"


def _rows(table) -> list[list[str]]:
    out = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if cells:
            out.append(cells)
    return out


def _signature(table) -> tuple[str, ...]:
    return tuple(th.get_text(" ", strip=True) for th in table.find_all("th"))


def _owning_id(table) -> str:
    """Id of the PrimeFaces widget wrapping this <table> (the <table> itself has none)."""
    for parent in table.parents:
        if parent.get("id"):
            return parent["id"]
    return ""


def parse_course(html_text: str, course_nr: str, semester: str) -> Course:
    soup = BeautifulSoup(html_text, "html.parser")
    course = Course(course_nr=course_nr, semester=semester)

    h1 = soup.find("h1")
    if h1:
        course.title = re.sub(r"\s+", " ", h1.get_text(" ", strip=True))

    # rows actually rendered vs rows that exist, per paginated widget
    totals = {m.group("id"): (int(m.group("rows")), int(m.group("total")))
              for m in PAGINATOR.finditer(html_text)}

    def note_if_partial(table) -> None:
        shown, total = totals.get(_owning_id(table), (0, 0))
        if total > shown > 0:
            course.partial[_owning_id(table)] = (shown, total)

    seen_single = False
    for table in soup.find_all("table"):
        sig = _signature(table)
        note_if_partial(table)

        # Expanded per-occurrence list; preferred over the collapsed range table.
        if sig == SINGLE_DATES:
            seen_single = True
            for r in _rows(table):
                if len(r) < 5:
                    continue
                start, end = _times(r[2])
                course.occurrences.append(Occurrence(
                    date=_iso_date(r[1]), start=start, end=end,
                    location=r[3], description=r[4]))

        elif sig == GROUP_DATES:
            for r in _rows(table):
                if len(r) < 6:
                    continue
                start, end = _times(r[2])
                course.group_registration  # noqa: B018  (keep attribute order stable)
                course.occurrences.append(Occurrence(
                    date=_iso_date(r[3]), start=start, end=end,
                    location=r[4], description=r[5], group=r[0]))

        elif sig == EXAMS:
            for r in _rows(table):
                if len(r) < 8:
                    continue
                start, end = _times(r[1])
                course.exams.append(Exam(
                    title=r[7] or "Exam", date=_iso_date(r[2]), start=start, end=end,
                    room=r[3], mode=r[4],
                    apply_from=_iso_dt(r[5], 0), apply_to=_iso_dt(r[5], 1),
                    apply_via=r[6]))

        elif sig == COURSE_REG:
            for r in _rows(table):
                if len(r) >= 2:
                    course.registration = {
                        "begin": _iso_dt(r[0]), "end": _iso_dt(r[1]),
                        "deregistration_end": _iso_dt(r[2]) if len(r) > 2 else "",
                    }

        elif sig == GROUP_REG:
            for r in _rows(table):
                if len(r) >= 3:
                    course.group_registration.append(
                        {"group": r[0], "from": _iso_dt(r[1]), "to": _iso_dt(r[2])})

    # Only fall back to the collapsed "05.10.2026 - 14.12.2026" ranges if TISS did not
    # ship the expanded list; a range cannot be turned into dates without guessing.
    if not seen_single:
        for table in soup.find_all("table"):
            if _signature(table) != COURSE_DATES:
                continue
            for r in _rows(table):
                if len(r) < 5:
                    continue
                start, end = _times(r[1])
                course.occurrences.append(Occurrence(
                    date=_iso_date(r[2]), start=start, end=end,
                    location=r[3], description=r[4] + " (series start; TISS gave a range)"))

    course.occurrences.sort(key=lambda o: (o.date, o.start))
    course.exams.sort(key=lambda e: e.date)
    return course


def scrape(course_numbers: list[str], semester: str) -> list[Course]:
    sess = make_session()
    out = []
    for nr in course_numbers:
        out.append(parse_course(fetch_course(nr, semester, sess), nr, semester))
    return out


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _fmt_dt(iso: str) -> str:
    if not iso:
        return "?"
    try:
        return datetime.fromisoformat(iso).strftime("%a %d.%m.%Y %H:%M")
    except ValueError:
        return iso


def report(courses: list[Course]) -> None:
    now = datetime.now()
    for c in courses:
        print("=" * 78)
        print(f"{c.course_nr}  {c.title or '(title not found)'}   [{c.semester}]")
        print("=" * 78)

        print(f"  lecture/exercise slots: {len(c.occurrences)}", end="")
        if c.occurrences:
            print(f"   {c.occurrences[0].date} .. {c.occurrences[-1].date}")
        else:
            print()

        if c.partial:
            for tid, (shown, total) in c.partial.items():
                print(f"  INCOMPLETE: {tid.split(':')[-1]} shows {shown} of {total} rows "
                      f"- TISS paginates it and the rest cannot be fetched.")
            print("              Use the iCal feed for lecture dates; it is complete.")

        if c.registration:
            print(f"  course registration : {_fmt_dt(c.registration.get('begin',''))}"
                  f"  ->  {_fmt_dt(c.registration.get('end',''))}")
            if c.registration.get("deregistration_end"):
                print(f"  deregistration until: {_fmt_dt(c.registration['deregistration_end'])}")

        for g in c.group_registration:
            print(f"  group reg: {g['group'][:34]:<34} {_fmt_dt(g['from'])} -> {_fmt_dt(g['to'])}")

        if not c.exams:
            print("  exams: none listed")
        else:
            print(f"  exams: {len(c.exams)}")
            for e in c.exams:
                print(f"    - {e.title}  {_fmt_dt(e.date + 'T' + (e.start or '00:00'))}"
                      f"-{e.end}  {e.mode}  {e.room}")
                if e.apply_from:
                    opens = ""
                    try:
                        delta = (datetime.fromisoformat(e.apply_from) - now).days
                        opens = f"  (opens in {delta} days)" if delta > 0 else "  (OPEN NOW)"
                    except ValueError:
                        pass
                    print(f"        register {_fmt_dt(e.apply_from)} -> "
                          f"{_fmt_dt(e.apply_to)} via {e.apply_via}{opens}")
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("courses", nargs="*", help="course numbers, e.g. 186814 or 186.814")
    ap.add_argument("-s", "--semester", default="2026W")
    ap.add_argument("-c", "--config", help="read [scrape].courses from this config file")
    ap.add_argument("--json", action="store_true", help="dump raw parsed data as JSON")
    args = ap.parse_args()

    numbers = list(args.courses)
    if args.config:
        with open(args.config, "rb") as f:
            cfg = tomllib.load(f)
        numbers += [str(x) for x in cfg.get("scrape", {}).get("courses", [])]
        args.semester = cfg.get("scrape", {}).get("semester", args.semester)
    if not numbers:
        ap.error("give at least one course number, or a config with [scrape].courses")

    courses = scrape(numbers, args.semester)
    if args.json:
        print(json.dumps([asdict(c) for c in courses], indent=2, ensure_ascii=False))
    else:
        report(courses)


if __name__ == "__main__":
    main()
