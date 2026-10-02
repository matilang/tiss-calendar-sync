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

2. **Config** — copy `config.example.toml` to `config.toml`, paste your TISS iCal link
   (TISS → your calendar → iCal export) and set the semester dates. Then

   ```bash
   python tiss_sync.py list       # every course in the feed, with first/last date
   python tiss_sync.py preview    # exactly what would be synced, writes nothing
   ```

   and put the courses you want into `courses`. `config.example.toml` documents every
   option; the ones worth knowing about are described under *Behaviour* below.

3. **Google access** — two ways in.

   **Service account (recommended, needed for unattended runs).** No browser, and no
   token that expires:

   - Google Cloud Console → create a project → enable the **Google Calendar API**.
   - *IAM & Admin → Service Accounts* → create one. It needs no project role.
   - *Keys* → Add key → JSON. Save it here as `service_account.json`.
   - In Google Calendar create a calendar ("TU Wien"), open *Settings and sharing* →
     *Share with specific people* → add the service account's address
     (`…@…iam.gserviceaccount.com`) with **Make changes to events**.
   - Put that calendar's ID into `config.toml`.

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
python tiss_sync.py list|preview|export|sync [-c config.toml]
python tiss_scrape.py 186814 194187        # exam dates, registration windows, group hours
python tiss_scrape.py 186814 --json
python cleanup_imported.py [--apply]       # remove events imported into the calendar by hand
python check_cloud_run.py --canary         # prove a GitHub Actions run reached the calendar
python -m pytest                           # 79 tests, no network (pip install -r requirements-dev.txt)
```

`tiss_sync.py` and `tiss_scrape.py` are thin entry points; the code lives in `tisscal/`
and the same commands work as `python -m tisscal.cli` and `python -m tisscal.scrape`.

## Where the code is

| module | what is in it |
|---|---|
| `tisscal/model.py` | the `Lecture` event type and the constants defining event identity |
| `tisscal/config.py` | reading `config.toml`, defaulting every option |
| `tisscal/feed.py` | fetching and parsing an iCal feed |
| `tisscal/pipeline.py` | **the rules**: filtering, placeholders, exercises, merging, titles, pruning |
| `tisscal/scrape.py` | reading exam dates and registration windows off a TISS course page |
| `tisscal/events.py` | turning scraped course data into calendar events |
| `tisscal/gcal.py` | Google Calendar: event bodies, retries, the sync |
| `tisscal/cli.py` | `list` / `preview` / `export` / `sync`, and the pipeline order |

If you want to change *what shows up on the calendar*, it is almost always
`tisscal/pipeline.py` - those are pure functions, events in and events out, and they are
what the tests cover.

`tiss_scrape.py` is worth running on its own before registering: it lists each exercise
group with its exact hours, so you can pick a group on the timetable rather than guessing.

## Behaviour worth knowing

**Exercise placeholders are hidden.** Before you pick an exercise group, TISS publishes
the whole span the groups run in — often once per room, e.g. Friday 09:00–19:00 twice —
although your slot will be one hour in one room. Any timed event at least
`placeholder_min_hours` long (default 4; real lectures run 1–2 h) is skipped. Once you
register, TISS emits your actual slot and the next sync picks it up.

**Past events are pruned, selectively.** With `[retention] prune_past = true`, lectures
and expired reminders are removed once they are over, while exams and exercise sessions
are kept so you can still look up when they were.

**Titles are yours to set.** `[titles]` maps a course number to a short name, and the
number is dropped from the title: `186.814 VU Algorithmics` → `VU Algorithmics`,
`VU Management of Graph Data` → `VU MoGD`. TUWEL titles never name their course, so the
short name is put in front there instead.

**TUWEL needs no extra code.** It is just another `.ics` URL — see `tuwel.example.toml`.
Events are tagged per config filename (`tiss_sync-tuwel` vs `tiss_sync-config`), so two
configs can never delete each other's events. TUWEL keeps previous years' course
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
laptop does not need to be on. In a **private** repository, add three secrets under
*Settings → Secrets and variables → Actions*:

| secret | contents |
|---|---|
| `SERVICE_ACCOUNT_JSON` | all of `service_account.json` |
| `CONFIG_TOML` | all of `config.toml` |
| `TUWEL_TOML` | all of `tuwel.toml` (optional; omit to skip TUWEL) |

Two caveats: GitHub disables scheduled workflows in a repository with no activity for 60
days, and the secrets are a frozen copy — change `config.toml` locally and you must update
the secret too, or the cloud keeps syncing the old version.

## Notes

- Course numbers are detected with the pattern `123.ABC`. If TISS titles look different in
  your feed, use part of the course title in `courses` instead.
- TISS course pages are rendered client-side. `tiss_scrape.py` replays the DeltaSpike
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
