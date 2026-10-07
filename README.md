# tiss-calendar-sync

Your TU Wien timetable, exams and TUWEL deadlines in Google Calendar, kept up to date on
their own.

TISS can export a calendar feed, but it is all of your courses and none of your exams. This
takes that feed, keeps the courses you pick, reads the exam dates and registration windows
off the TISS course pages, and syncs the result into a Google calendar with reminders. Then
it does that every morning without your laptop being on.

You pick what gets synced on a local page that shows you the month before and after your
change, so nothing reaches the calendar that you have not seen first.

![The interface: a month of the calendar with the effect of ticking one more course drawn
over it — new events outlined in green, and a strip of months saying how many fall in
each](docs/interface.png)

*Ticking one course here would add 31 events. The grid says where they land before anything
is written.*

Python 3.11 or newer.

## What ends up on the calendar

| | where it comes from | reminder |
|---|---|---|
| lectures | the TISS iCal feed | 15 min before |
| exercise slots | the feed, retitled `UE` and given their own colour | 15 min before |
| exams, tests, retakes | scraped from the TISS course page | 3 days + 1 day before, red |
| registration windows | scraped | 1 day before and when it opens, yellow |
| TUWEL deadlines | the TUWEL calendar export | 3 days + 1 day before, orange |

Re-running is safe. Events are updated in place, never duplicated, and anything that
disappears from TISS is removed. The sync only ever touches events it created itself, marked
with a private tag, so the rest of your calendar is left alone.

## Setup

### 1. Install

```bash
git clone https://github.com/matilang/tiss-calendar-sync.git
cd tiss-calendar-sync
python -m venv .venv && .venv\Scripts\activate     # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
git config core.hooksPath tools/githooks           # refuses to commit a secret
```

### 2. Give it a calendar to write to

In Google Calendar, **create a new calendar** — call it `TU Wien`. Keeping it separate means
a mistake here can never touch your real one.

Then make an identity for the sync to use. A service account rather than your own login,
because its key does not expire and needs no browser, which is what an unattended daily run
requires:

1. [Create a Google Cloud project](https://console.cloud.google.com/projectcreate) — any
   name, no billing needed.
2. [Enable the Google Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com)
   for it.
3. [Create a service account](https://console.cloud.google.com/iam-admin/serviceaccounts) —
   any name, and **skip the optional role step**; it needs no project permissions at all.
4. Open it → **Keys** → *Add key* → *Create new key* → **JSON**. Save the downloaded file in
   this folder as `service_account.json`.
5. Back in Google Calendar: your new calendar → *Settings and sharing* → **Share with
   specific people** → add the service account's address, which looks like
   `something@your-project.iam.gserviceaccount.com`, with **Make changes to events**.
6. On the same page, copy the **Calendar ID** — a long string ending in
   `@group.calendar.google.com`.

The service account can see nothing except the calendars you share with it explicitly.

### 3. Fill in your two private values

```bash
cp .secrets.example.toml .secrets.toml
```

It needs the calendar ID from the step above, and your TISS feed link: in TISS, open
**Calendar** and use *iCal export* / the subscribe link. That link contains a token that is
as good as your login, which is why this file is never committed.

TUWEL is optional and works the same way — see the comments in `.secrets.example.toml`.

### 4. Pick your courses

```bash
python tiss_sync.py ui
```

This opens a page on `127.0.0.1` where you tick the courses you want, see exactly what that
would add or remove, and save. See [The interface](#the-interface) below.

### 5. Let it run by itself

The sync runs daily on GitHub Actions, so nothing has to be switched on at 7 a.m. Push your
repository to GitHub, then under *Settings → Secrets and variables → Actions* add:

| secret | contents |
|---|---|
| `SERVICE_ACCOUNT_JSON` | the whole contents of `service_account.json` |
| `TISS_ICAL_URL` | your TISS iCal link |
| `GOOGLE_CALENDAR_ID` | the calendar ID |
| `TUWEL_ICAL_URL` | your TUWEL export link — optional, omit to skip TUWEL |

Paste the **values only**, without the surrounding quotes. Then run the workflow once by
hand from the *Actions* tab to check it.

Because your settings live in the repository and only the private values are secrets,
**adding a course later is an ordinary commit** — the secrets change only when a token does.

Two things to know: `settings.toml` lists the courses you take, so decide accordingly if the
repository is public; and GitHub turns off scheduled workflows in a repository with no
activity for 60 days.

<details>
<summary>Running it on your own machine instead</summary>

`sync_daily.cmd` runs both profiles and appends to `sync.log`. Point Windows Task Scheduler
at it, or call `python tiss_sync.py sync` from any scheduler you already use. The catch is
the obvious one: a run scheduled for a time the machine is off does not happen, which is why
the cloud is the default.

</details>

## The interface

```bash
python tiss_sync.py ui
```

One local page, four parts.

**Courses** — every course in your TISS feed, *including the ones you have not picked*. A
course you have just been admitted to shows up here before it is anywhere else, highlighted.
Per course you can tick:

- **Sync** — put its events on the calendar
- **Scrape** — also read its TISS page for exam dates and registration windows
- **Calendar name** — what it is called on the calendar, e.g. `VU MoGD` instead of
  `192.161 VU Management of Graph Data`

**What gets synced** — the switches that are not per course: reminder times, whether past
events are pruned, which registration windows to be reminded about. Each one shows the
setting it writes, so the page explains the file rather than hiding it.

**Your calendar** — a month grid of what is on the calendar now, with your unsaved changes
drawn on top:

| | |
|---|---|
| outlined green, `+` | would be added |
| struck through, `−` | would be removed |
| outlined amber, `~` | would change — the tooltip says which fields |

The strip of months above the grid marks how many changes fall in each, so you can see at a
glance *where* ticking a course would put things, and click straight there. A flat
chronological list is one button away when you want the detail instead.

**Git** — whether the daily run in the cloud is using what you are looking at, and a button
to commit and push if it is not.

Nothing is written until you press **Apply**. When you do, the comments in `settings.toml`
are preserved and the result is checked before it replaces the file. If you edited the same
file in an editor meanwhile, the save is refused rather than overwriting your work.

The server listens on `127.0.0.1` only, and the page is never sent your feed link or
calendar ID — only whether they are set.

## How it works

The feed and the scraped course pages go through one sequence of rules, and the result is
compared with what is already on the calendar. That sequence is `tisscal/pipeline.py`, and it
is the file to read first — the steps are pure functions, events in and events out.

| module | what is in it |
|---|---|
| `pipeline.py` | **the rules**, in the order they apply |
| `filters.py` | which events belong on the calendar at all |
| `classify.py` | is this an exercise? an exam? |
| `titles.py` | what an event is called |
| `plan.py` | what a settings change would add, remove or alter — without calling Google |
| `scrape.py` | exam dates and registration windows from a TISS course page |
| `events.py` | turning scraped course data into calendar events |
| `gcal.py` | Google Calendar: event bodies, retries, the sync itself |
| `config.py` / `secrets.py` | every option, and where the private values come from |
| `settings_io.py` | writing `settings.toml` back in place, comments intact |
| `web.py` / `cache.py` / `vcs.py` | the interface, and what makes it quick and honest |
| `model.py` / `feed.py` / `merge.py` / `cli.py` | the event type and its identity, fetching and parsing the feed, folding one slot held in two rooms into one event, and the command line |

`python -m pytest` runs the tests. They need no network and no credentials.

<details>
<summary>Command line, if you prefer it</summary>

```bash
python tiss_sync.py sync                   # what the daily run does
python tiss_sync.py preview                # what would be synced, writes nothing
python tiss_sync.py preview --diff         # what your uncommitted edits would change
python tiss_sync.py list                   # every course in the feed
python tiss_sync.py export                 # a cleaned .ics file, no Google needed
python -m tisscal.scrape 186814            # exam dates, registration windows, group hours
```

Running the scraper on its own is worth it before choosing an exercise group: it prints each
group with its exact hours, so you can pick one against your timetable instead of guessing.

</details>

## Behaviour worth knowing

**Exercise placeholders are skipped.** Before you pick a group, TISS publishes the whole span
the groups run in — often once per room, e.g. Friday 09:00–19:00 twice — even though your
slot will be one hour in one room. Any timed event at least `placeholder_min_hours` long
(default 4; real lectures run 1–2 h) is left out. Once you register, your actual slot appears
in the feed and the next sync picks it up.

**Exercises are retitled and coloured.** A VU is a lecture course that also has exercises, so
the course does not change — only the type code, `VU ASE` → `UE ASE`, plus a colour of its
own. Recognising one takes two rules, because TISS describes the slot only until you are
registered, after which it names it after the group and the hour:

```
before registering:  Exercise sessions                                  (10 h, per room)
after registering:   194.187 Advanced Software Engineering 3_11:00-12:00
```

The first matches `[exercises] keywords`, the second its shape — see `tisscal/classify.py`.
Q&A sessions are deliberately left as lectures.

**Past events are pruned, selectively.** With `[retention] prune_past = true`, lectures and
expired reminders go once they are over, while exams and exercise sessions stay so you can
still look up when they were.

**TUWEL needs no extra code.** It is just another `.ics` URL, configured in
`settings.tuwel.toml`. Events are tagged per profile, so the two syncs can share one calendar
and neither can delete the other's events. TUWEL keeps previous years' course instances,
which is why the course is read from the iCal `CATEGORIES` field and stale semesters can be
dropped with `exclude_keywords`.

## Notes

- Course numbers are matched as `123.ABC`. If your feed titles look different, put part of
  the course title in `courses` instead.
- TISS course pages are rendered client-side, so the scraper replays the DeltaSpike window
  handshake (a `dsrwid-<token>` cookie plus `?dsrid=<token>`) to get the real HTML — no
  headless browser needed. The lecture-dates table is paginated at 20 rows and the rest
  cannot be fetched, so the scraper reports `INCOMPLETE: shows 20 of 27 rows` rather than
  quietly truncating. Take lecture dates from the iCal feed, which is complete.
- TISS stamps every iCal UID with the time the feed was generated, so event identity is the
  UID with that timestamp removed. Without that, every sync would delete and recreate the
  whole semester.
- If HTTPS fails with `CERTIFICATE_VERIFY_FAILED`, something local — antivirus, a corporate
  proxy — is re-signing traffic with a root that the OS trusts but Python's `certifi` does
  not. `truststore` is in `requirements.txt` and handles it. For `git` itself the equivalent
  is `git config http.sslBackend schannel` on Windows.
- Signing in with your own Google account instead of a service account works
  (`credentials.json` from an OAuth *Desktop app* client), but while the OAuth app is in
  "Testing" Google expires the refresh token after 7 days, which breaks any scheduled run.

## Licence

MIT.
