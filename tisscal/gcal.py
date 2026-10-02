"""Talking to Google Calendar: the event body, the retries, and the sync itself."""
from __future__ import annotations

import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import sleep

from .model import VIENNA, Lecture
from .pipeline import display_title, is_exam


def _gcal_body(ev: Lecture, cfg: dict) -> dict:
    def ts(v):
        if isinstance(v, datetime):
            return {"dateTime": v.isoformat(), "timeZone": "Europe/Vienna"}
        return {"date": v.isoformat()}

    rem = cfg["reminders"]
    if ev.kind == "registration":
        # Exam sign-up is worth a warning days ahead. An exercise group is
        # first-come-first-served and opens at a published hour, so a day-early nudge is
        # useless there - the only moment that matters is the moment it opens.
        default = [24 * 60, 0]
        minutes = rem.get(f"{ev.scope}_registration_minutes_before",
                          rem.get("registration_minutes_before", default))
        color = rem.get("registration_color_id", "5")   # 5 = banana
    elif ev.kind == "exam" or is_exam(ev, cfg):
        minutes = rem.get("exam_minutes_before", [3 * 24 * 60, 24 * 60])
        color = rem.get("exam_color_id", "11")          # 11 = red
    else:
        minutes = rem.get("lecture_minutes_before", [15])
        color = None

    title = display_title(ev, cfg)

    body = {
        "summary": title,
        "location": ev.location,
        "description": ev.description,
        "start": ts(ev.start),
        "end": ts(ev.end),
        "reminders": {"useDefault": False,
                      "overrides": [{"method": "popup", "minutes": m} for m in minutes]},
        "extendedProperties": {"private": {"source": cfg["_tag"]}},
        "status": "confirmed",
    }
    if color:
        body["colorId"] = color
    return body


RETRY_STATUS = {403, 429, 500, 502, 503, 504}


THROTTLE_REASONS = ("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded")


def _execute(request, tries: int = 6):
    """Run a Google API request, backing off on throttling and transient server errors.

    Without this a single rate-limit response aborts the sync halfway through, leaving
    part of the semester on the calendar. 409 is passed straight through: cmd_sync uses
    it to tell "already exists" apart from a real failure.
    """
    from googleapiclient.errors import HttpError

    for attempt in range(tries):
        try:
            return request.execute()
        except HttpError as e:
            status = e.resp.status
            last = attempt == tries - 1
            if status == 409 or status not in RETRY_STATUS or last:
                raise
            if status == 403 and not any(r in str(e) for r in THROTTLE_REASONS):
                raise  # a genuine permission error, not throttling
            delay = 2 ** attempt + random.uniform(0, 1)
            print(f"  Google returned {status}; retrying in {delay:.1f}s",
                  file=sys.stderr)
            sleep(delay)


def cmd_sync(events: list[Lecture], cfg: dict, base: Path) -> None:
    from googleapiclient.errors import HttpError
    from gcal_auth import get_service

    svc = get_service(base)
    cal_id = cfg["google"]["calendar_id"]
    wanted = {ev.gcal_id: ev for ev in events}

    created = updated = revived = deleted = 0
    for gid, ev in wanted.items():
        body = _gcal_body(ev, cfg) | {"id": gid}
        try:
            _execute(svc.events().insert(calendarId=cal_id, body=body))
            created += 1
        except HttpError as e:
            if e.resp.status != 409:
                raise
            # 409 means the id is taken - but that includes ids Google is only holding as
            # a tombstone for an event deleted earlier. For those, update returns 200 and
            # leaves status "cancelled", so the event stays invisible while the sync
            # happily reports it as updated. Only an explicit patch brings it back.
            resp = _execute(svc.events().update(calendarId=cal_id, eventId=gid, body=body))
            if resp.get("status") == "cancelled":
                _execute(svc.events().patch(calendarId=cal_id, eventId=gid,
                                            body={"status": "confirmed"}))
                revived += 1
            else:
                updated += 1

    # Remove events this script created earlier that are no longer in the filtered feed
    # (e.g. you removed a course from config, or TISS cancelled a lecture).
    sem = cfg.get("semester", {})
    t_min = datetime.combine(date.fromisoformat(sem["start"]) if "start" in sem else date.today(),
                             time.min, VIENNA).isoformat()
    page = None
    while True:
        resp = _execute(svc.events().list(
            calendarId=cal_id, timeMin=t_min, pageToken=page,
            privateExtendedProperty=f"source={cfg['_tag']}",
            singleEvents=True, maxResults=2500))
        for item in resp.get("items", []):
            if item["id"] not in wanted:
                _execute(svc.events().delete(calendarId=cal_id, eventId=item["id"]))
                deleted += 1
        page = resp.get("nextPageToken")
        if not page:
            break

    report = f"Done: {created} created, {updated} updated, {deleted} removed"
    if revived:
        report += f", {revived} restored after an earlier deletion"
    print(report + ".")
