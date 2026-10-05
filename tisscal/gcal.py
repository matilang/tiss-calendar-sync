"""Talking to Google Calendar: the event body, the retries, and the sync itself."""
from __future__ import annotations

import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import sleep

from .model import VIENNA, Lecture
from .classify import is_exam, is_exercise
from .config import Settings
from .titles import display_title


def _gcal_body(ev: Lecture, settings: Settings) -> dict:
    def ts(v):
        if isinstance(v, datetime):
            return {"dateTime": v.isoformat(), "timeZone": "Europe/Vienna"}
        return {"date": v.isoformat()}

    rem = settings.reminders
    if ev.kind == "registration":
        # Reminders.for_scope picks the per-scope override when there is one: exam
        # sign-up wants a warning days ahead, an exercise group only the moment it opens.
        minutes = rem.for_scope(ev.scope)
        color = rem.registration_color_id
    elif ev.kind == "exam" or is_exam(ev, rem):
        minutes = rem.exam_minutes_before
        color = rem.exam_color_id
    else:
        minutes = rem.lecture_minutes_before
        # An exercise keeps the lecture's reminders - it is a slot you attend - but gets its
        # own colour, because the point of telling it apart is telling it apart at a glance.
        color = (settings.exercises.color_id
                 if is_exercise(ev, settings.exercises) else None)

    title = display_title(ev, settings.titles, settings.exercises)

    body = {
        "summary": title,
        "location": ev.location,
        "description": ev.description,
        "start": ts(ev.start),
        "end": ts(ev.end),
        "reminders": {"useDefault": False,
                      "overrides": [{"method": "popup", "minutes": m} for m in minutes]},
        "extendedProperties": {"private": {"source": settings.tag}},
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


def cmd_sync(events: list[Lecture], settings: Settings, base: Path) -> None:
    from googleapiclient.errors import HttpError
    from gcal_auth import get_service

    if not settings.calendar_id:
        # Checked here rather than at load time: list, preview and export all work
        # without a calendar, and only this command writes to one.
        from . import secrets as sec

        sys.exit(f"No calendar id for profile {settings.profile!r}. Set one of:\n"
                 f"{sec.sources('calendar_id', settings.profile)}")

    svc = get_service(base)
    cal_id = settings.calendar_id
    wanted = {ev.gcal_id: ev for ev in events}

    created = updated = revived = deleted = 0
    for gid, ev in wanted.items():
        body = _gcal_body(ev, settings) | {"id": gid}
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
    t_min = datetime.combine(settings.semester.start, time.min, VIENNA).isoformat()
    page = None
    while True:
        resp = _execute(svc.events().list(
            calendarId=cal_id, timeMin=t_min, pageToken=page,
            privateExtendedProperty=f"source={settings.tag}",
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
