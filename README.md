# tiss-calendar-sinc

App allowing TU wien students to cleanly copy their schedule and deadlines to google calendar.

Takes your TISS calendar feed, keeps only the courses you pick, scrapes the exam dates and
registration deadlines that the feed leaves out, and syncs the result into Google Calendar
with reminders. Optionally does the same for TUWEL assignment deadlines.

Requires Python 3.11+ (the config is read with `tomllib`).

## What ends up on the calendar

| | from | reminder |
|---|---|---|
| lectures and exercise slots | TISS iCal feed | 15 min before |
| exams, tests, retakes | scraped from the TISS course page | 3 days + 1 day before, red |
| exam registration windows | scraped | 1 day before + when it opens, yellow |
| TUWEL deadlines | TUWEL calendar export | 3 days + 1 day before, orange |

## Setup

1. **Install**

   ```bash
   python -m venv .venv && .venv\Scripts\activate    # Linux/macOS: source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Settings and secrets are separate files.** `settings.toml` holds the courses,
   titles, reminders and filters and **is committed**; `.secrets.toml` holds the two
   private values and is **never** committed. Copy `.secrets.example.toml` to
   `.secrets.toml` and paste your TISS iCal link (TISS → your calendar → iCal export)
   and your calendar ID into it. Then

   ```bash
   python tiss_sync.py list       # every course in the feed, with first/last date
   python tiss_sync.py preview    # exactly what would be synced, writes nothing
   ```

   and put the courses you want into `courses` in `settings.toml`, which documents
   every option inline; the ones worth knowing about are under *Behaviour* below.

   Either value can also come from the environment — `TISSCAL_ICAL_URL` and
   `TISSCAL_CALENDAR_ID` take precedence over the file, which is how the cloud run gets
   them without a copy of the settings living in a repository secret.

   Then install the commit guard, once per clone:

   ```bash
   git config core.hooksPath tools/githooks
   ```

   It refuses to commit `.secrets.toml`, a service account key, or anything shaped like a
   feed token — *before* the commit exists. The test suite checks the same thing, but that
   runs in CI, which is after the push; on a public repository the difference is between
   amending a commit and rotating three credentials. `--no-verify` goes past it on purpose.

3. **Google access** — two ways in.

   **Service account (recommended, needed for unattended runs).** No browser, and no
   token that expires:

   - Google Cloud Console → create a project → enable the **Google Calendar API**.
   - *IAM & Admin → Service Accounts* → create one. It needs no project role.
   - *Keys* → Add key → JSON. Save it here as `service_account.json`.
   - In Google Calendar create a calendar ("TU Wien"), open *Settings and sharing* →
     *Share with specific people* → add the service account's address
     (`…@…iam.gserviceaccount.com`) with **Make changes to events**.
   - Put that calendar's ID into `.secrets.toml` (or `TISSCAL_CALENDAR_ID`).

   **Your own Google account (OAuth).** *Credentials* → OAuth client ID → type
   **Desktop app** → save as `credentials.json`. The first sync opens a browser.
   Be aware: while the OAuth app's publishing status is "Testing", Google expires the
   refresh token after **7 days**, so a scheduled sync breaks weekly. Publishing the app
   would fix it, but Google requires a branding page with a homepage and privacy policy
   on a domain you own — which is why the service account exists above.

4. **Sync**

   ```bash
   python tiss_sync.py sync
   ```

   Re-running is safe: events are updated in place, never duplicated, and anything that
   disappears from TISS (or that you remove from the config) is deleted. The script only
   ever touches events it created itself, identified by a private `source` tag, so other
   events on the same calendar are left alone.

No Google setup wanted? `python tiss_sync.py export` writes `tiss_clean.ics` for manual
import — but then there are no automatic updates.

## Commands

```bash
python tiss_sync.py ui                     # the local interface (opens a browser)
python tiss_sync.py list|preview|export|sync [-c settings.toml]
python tiss_sync.py preview --diff         # what your uncommitted settings edits change
python tiss_sync.py preview --diff other.toml   # ...or against another settings file
python -m tisscal.scrape 186814 194187     # exam dates, registration windows, group hours
python -m tisscal.scrape 186814 --json
python tools/cleanup_imported.py [--apply] # remove events imported into the calendar by hand
python check_cloud_run.py --canary         # prove a GitHub Actions run reached the calendar
python -m pytest                           # no network needed (pip install -r requirements-dev.txt)
```

`tiss_sync.py` is a thin entry point; the code lives in `tisscal/`
and the same commands work as `python -m tisscal.cli` and `python -m tisscal.scrape`.

## The interface

`python tiss_sync.py ui` opens a local page for changing the settings and seeing what the
change would do before it is written.

| | |
|---|---|
| **Courses** | every course in your TISS feed, including the ones **not** in `settings.toml` - a course you have just been admitted to appears there first, and this is where you notice. Per course: sync it, scrape its exam dates, and what it is called on the calendar. |
| **What gets synced** | the switches that are not per course - reminders, pruning, the placeholder threshold, which registration windows to be reminded about. Each shows the option name it writes, so the page teaches the file rather than hiding it. |
| **What this would change** | the point of the whole thing: the events that would appear, disappear or come out different, worked out by running the pipeline twice and comparing what would be sent to Google. Compared against the **committed** settings, because that is what the daily sync runs. |
| **Git** | whether the file is committed and pushed. A saved file is not yet a changed calendar; the cloud pulls from the remote. One button closes the gap. |

Nothing is written until *Apply*, which goes through the same writer as everything else:
comments in `settings.toml` survive, and the file is validated before it replaces the
original. If you also edited the file in an editor meanwhile, the save is refused rather
than silently overwriting it.

The server listens on **127.0.0.1 only** and has no authentication, which is fine because
nothing else can reach it - and the reason not to move it off localhost without adding some.
The page is never sent the feed URL or the calendar id, only whether they are set: a browser
tab is a place secrets leak from.

The feed and the course pages are cached in `.cache/` so a click costs no HTTP requests;
*Refresh data* is the only thing that refetches, which makes staleness visible instead of
accidental.

## Where the code is

| module | what is in it |
|---|---|
| `tisscal/model.py` | the `Lecture` event type and the constants defining event identity |
| `tisscal/config.py` | reading `settings.toml`, defaulting every option |
| `tisscal/secrets.py` | where the iCal URL and calendar ID come from, separately |
| `tisscal/feed.py` | fetching and parsing an iCal feed |
| `tisscal/pipeline.py` | **the rules**: filtering, placeholders, scraping, merging, pruning |
| `tisscal/plan.py` | what a settings change would add, remove or alter - no Google calls |
| `tisscal/settings_io.py` | writing `settings.toml` back in place, comments intact |
| `tisscal/cache.py` | the feed and the TISS course pages on disk, so a click is cheap |
| `tisscal/vcs.py` | the little bit of git the interface needs |
| `tisscal/web.py` | the local interface: one page and six endpoints |
| `tisscal/scrape.py` | reading exam dates and registration windows off a TISS course page |
| `tisscal/events.py` | turning scraped course data into calendar events |
| `tisscal/gcal.py` | Google Calendar: event bodies, retries, the sync |
| `tisscal/cli.py` | `list` / `preview` / `export` / `sync`, and the pipeline order |

If you want to change *what shows up on the calendar*, it is almost always
`tisscal/pipeline.py` - those are pure functions, events in and events out, and they are
what the tests cover.

The scraper is worth running on its own before registering: it lists each exercise
group with its exact hours, so you can pick a group on the timetable rather than guessing.

## Behaviour worth knowing

**Exercise placeholders are hidden.** Before you pick an exercise group, TISS publishes
the whole span the groups run in — often once per room, e.g. Friday 09:00–19:00 twice —
although your slot will be one hour in one room. Any timed event at least
`placeholder_min_hours` long (default 4; real lectures run 1–2 h) is skipped. Once you
register, TISS emits your actual slot and the next sync picks it up.

**Exercises are retitled and coloured.** A VU is a lecture course that also has exercises,
so the course does not change — only the type code: `VU ASE` → `UE ASE`, plus the colour
from `[exercises] color_id`. Spotting an exercise takes two tries, because TISS describes
the slot only until you are registered for a group, after which it names it after the group
and the hour instead:

```
before registering:  Exercise sessions                              (10 h, once per room)
after registering:   194.187 Advanced Software Engineering 3_11:00-12:00   (your hour)
```

The first is matched by `[exercises] keywords`, the second by its shape — see
`tisscal/classify.py`. Until this was noticed, a registered exercise arrived on the calendar
titled `VU ASE`, with nothing to tell it from the lectures around it. Q&A sessions
deliberately stay lectures.

**Past events are pruned, selectively.** With `[retention] prune_past = true`, lectures
and expired reminders are removed once they are over, while exams and exercise sessions
are kept so you can still look up when they were.

**Titles are yours to set.** `[titles]` maps a course number to a short name, and the
number is dropped from the title: `186.814 VU Algorithmics` → `VU Algorithmics`,
`VU Management of Graph Data` → `VU MoGD`. TUWEL titles never name their course, so the
short name is put in front there instead.

**TUWEL needs no extra code.** It is just another `.ics` URL — see `settings.tuwel.toml`.
Events are tagged per profile (`tiss_sync-tuwel` vs `tiss_sync-config`), so two profiles
can never delete each other's events. The profile is named by the `profile` key rather
than taken from the file name, so renaming a settings file cannot orphan the events it
already owns. TUWEL keeps previous years' course
instances, which is why the course is read from the iCal `CATEGORIES` field
(`192.161-2026W`) and stale semesters can be excluded with `exclude_keywords`.

## Running it automatically

**Your laptop** — `sync_daily.cmd` runs both configs and logs to `sync.log`:

```
schtasks /create /tn "TISS calendar sync" /tr "\"%CD%\sync_daily.cmd\"" /sc daily /st 07:00
```

Tick *"Run task as soon as possible after a scheduled start is missed"* in `taskschd.msc`,
otherwise a run is simply skipped when the machine is off.

**Always-on** — `.github/workflows/sync.yml` runs it daily on GitHub Actions, so the
laptop does not need to be on. The settings come from the checkout; only the private
values are secrets. Under *Settings → Secrets and variables → Actions*:

| secret | contents |
|---|---|
| `SERVICE_ACCOUNT_JSON` | all of `service_account.json` |
| `TISS_ICAL_URL` | the TISS iCal link |
| `GOOGLE_CALENDAR_ID` | the target calendar's ID |
| `TUWEL_ICAL_URL` | the TUWEL export link (optional; omit to skip TUWEL) |

Because the settings are in git, **adding a course is an ordinary commit** — the secrets
only change when a token does. An earlier version pasted all of `config.toml` into a
`CONFIG_TOML` secret, which meant every settings change had to be made twice; forgetting
the second half left the cloud quietly syncing an older course list.

Keep the repository **private** anyway: `settings.toml` names the courses you take.

One caveat remains: GitHub disables scheduled workflows in a repository with no activity
for 60 days.

## Notes

- Course numbers are detected with the pattern `123.ABC`. If TISS titles look different in
  your feed, use part of the course title in `courses` instead.
- TISS course pages are rendered client-side. `tisscal/scrape.py` replays the DeltaSpike
  window handshake (a `dsrwid-<token>` cookie plus `?dsrid=<token>`) to get the real HTML,
  so no headless browser is needed. The lecture-dates table is paginated at 20 rows and
  the remaining pages cannot be fetched, so the scraper reports
  `INCOMPLETE: shows 20 of 27 rows` rather than silently truncating — take lecture dates
  from the iCal feed, which is complete.
- TISS stamps every iCal UID with the time the feed was generated, so event identity comes
  from the UID with that timestamp stripped. Without it, every sync would delete and
  recreate the whole semester.
- If HTTPS fails with `CERTIFICATE_VERIFY_FAILED`, something local (antivirus, corporate
  proxy) is re-signing traffic with a root that is in the OS store but not in `certifi`.
  `truststore` is in `requirements.txt` and handles it. For `git` itself, the equivalent
  is `git config http.sslBackend schannel` on Windows.
