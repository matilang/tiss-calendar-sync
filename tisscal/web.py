"""A local page for changing the settings and seeing what it would do first.

Why a server at all, for a single file on the same machine: the page needs to run the
pipeline to answer "what would this change?", and the pipeline is Python. So this is a thin
HTTP layer over the same functions the command line uses - plan.compare for the difference,
settings_io.write for the saving, vcs for where git stands.

Built on http.server rather than a framework on purpose. requirements.txt is installed by
the GitHub Actions sync on every run, and a web framework there would be a dozen packages
that the sync never imports. Six endpoints do not need more than this.

Bound to 127.0.0.1 and nothing else. There is no authentication, because there is no
listening socket anyone else can reach - which also means this must not be moved to 0.0.0.0
without adding some.

What the page is never told: the feed URL and the calendar id. It is told whether they are
set, which is all it needs to show a warning, and never their values - a browser tab is a
place secrets leak from, through history, extensions and screenshots.
"""
from __future__ import annotations

import json
import threading
import tomllib
import traceback
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import vcs
from .cache import Cache
from .config import ConfigError, Settings, from_dict, load_config
from .feed import parse_feed
from .filters import matches_course
from .model import VIENNA, Lecture
from .pipeline import build_events
from .plan import Plan, compare, once
from .settings_io import WriteError, as_dict
from .settings_io import write as write_settings
from .titles import course_label

STATIC = Path(__file__).resolve().parent / "static"


# --------------------------------------------------------------------------- #
# What the page is shown
# --------------------------------------------------------------------------- #
def survey(raw: list[Lecture], built: list[Lecture], settings: Settings) -> list[dict]:
    """One row per course in the feed, whether or not the settings keep it.

    The courses *not* in the settings are the interesting half: a course you have just been
    admitted to appears in the feed before it appears in settings.toml, and until now the
    only way to notice was to run `list` and read it. 193.219 will show up here the day the
    registration is confirmed.
    """
    now = datetime.now(VIENNA)
    rows: dict[str, dict] = {}

    def row(ev: Lecture) -> dict:
        key = ev.course_nr or "(no course number)"
        if key not in rows:
            rows[key] = {"course_nr": ev.course_nr or "", "key": key,
                         "label": course_label(ev.course_nr, settings.titles),
                         "feed_title": ev.summary, "in_feed": 0, "synced": 0,
                         "scraped": 0, "in_config": False, "next": None}
        return rows[key]

    for ev in raw:
        r = row(ev)
        r["in_feed"] += 1
        # The real rule, not a re-implementation of it: a course counts as configured if
        # its events actually survive the course filter.
        if matches_course(ev, settings.courses):
            r["in_config"] = True

    for ev in built:
        r = row(ev)
        r["synced"] += 1
        # Exams and registration reminders come off the course page, not the feed, so a
        # course can legitimately have more events on the calendar than in the feed.
        # Counted separately because "synced 39, in feed 28" reads like a bug otherwise.
        if ev.kind in ("exam", "registration"):
            r["scraped"] += 1
        start = ev.start_dt
        if start >= now and (r["next"] is None or start.isoformat() < r["next"]):
            r["next"] = start.isoformat()

    return sorted(rows.values(), key=lambda r: (not r["synced"], r["key"]))


def plan_json(plan: Plan, limit: int = 500) -> dict:
    return {
        "summary": plan.summary,
        "counts": {"created": len(plan.created), "deleted": len(plan.deleted),
                   "updated": len(plan.updated), "unchanged": plan.unchanged},
        "truncated": len(plan.changes) > limit,
        "changes": [{"id": c.gcal_id, "kind": c.kind, "start": c.start, "title": c.title,
                     "location": c.location, "fields": list(c.fields),
                     "before": c.before, "after": c.after}
                    for c in plan.changes[:limit]],
    }


# --------------------------------------------------------------------------- #
# The state behind the page
# --------------------------------------------------------------------------- #
class Interface:
    """Everything the endpoints need, with the expensive parts held in memory.

    The feed and the parsed course pages are kept for the life of the process: recomputing a
    diff on every click must not re-read or re-parse them. `refresh` is the only way to get
    new ones, which makes staleness something the page can show rather than something that
    happens invisibly.
    """

    def __init__(self, root: Path, config_name: str = "settings.toml"):
        self.root = root
        self.path = root / config_name
        self.cache = Cache(root)
        self._feed: list[Lecture] | None = None
        self._scraper = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------- ingredients
    def settings(self) -> Settings:
        """Read from disk every time: the file is also edited in an editor."""
        return load_config(self.path)

    def feed(self, *, force: bool = False) -> list[Lecture]:
        with self._lock:
            if self._feed is None or force:
                self._feed = parse_feed(self.cache.feed(self.settings().ical_url,
                                                        force=force))
            return self._feed

    def scraper(self, *, force: bool = False):
        with self._lock:
            if self._scraper is None or force:
                # once() keeps the parsed Course objects in memory; the Cache keeps the
                # HTML on disk. Together: no HTTP and no re-parsing between clicks.
                self._scraper = once(self.cache.scraper(force=force))
            return self._scraper

    def proposed(self, payload: dict) -> Settings:
        """Settings as the page has them. Secrets are absent and stay absent."""
        return from_dict(payload, profile=self.settings().profile)

    # ---------------------------------------------------------------- endpoints
    def state(self) -> dict:
        settings = self.settings()
        raw = self.feed()
        built = build_events(raw, settings, scraper=self.scraper())
        return {
            "file": self.path.name,
            "profile": settings.profile,
            "mtime": self.path.stat().st_mtime,
            "settings": as_dict(settings),
            "courses": survey(raw, built, settings),
            "events": len(built),
            "cache": {name: round(age or 0) for name, age in self.cache.status().items()},
            "git": vcs.status(self.root),
            # Whether, never what.
            "secrets": {"ical_url": bool(settings.ical_url),
                        "calendar_id": bool(settings.calendar_id)},
        }

    def diff(self, payload: dict) -> dict:
        proposed = self.proposed(payload["settings"])
        raw, scraper = self.feed(), self.scraper()
        out = {"vs_file": plan_json(compare(raw, self.settings(), proposed,
                                            scraper=scraper))}
        committed = vcs.file_at(self.root, self.path.name, "HEAD")
        if committed is not None:
            head = from_dict(tomllib.loads(committed), profile=proposed.profile)
            out["vs_head"] = plan_json(compare(raw, head, proposed, scraper=scraper))
        return out

    def save(self, payload: dict) -> dict:
        proposed = self.proposed(payload["settings"])
        expected = payload.get("mtime")
        actual = self.path.stat().st_mtime
        if expected is not None and abs(expected - actual) > 1e-6:
            # The file is also edited in VS Code. Overwriting someone else's save silently
            # would be the worst thing this page could do.
            raise Conflict(f"{self.path.name} changed on disk since this page loaded. "
                           f"Reload to pick up the new version - your toggles will be lost, "
                           f"which is better than discarding the edit on disk.")
        changed = write_settings(self.path, proposed)
        return {"changed": {k: list(v) for k, v in changed.items()},
                "state": self.state()}

    def refresh(self) -> dict:
        self.cache.clear()
        self.feed(force=True)
        self.scraper(force=True)
        return self.state()

    def commit(self, payload: dict) -> dict:
        message = payload.get("message") or f"Update {self.path.name}"
        result = vcs.commit_push(self.root, [self.path.name], message,
                                 push=bool(payload.get("push", True)))
        result["git"] = vcs.status(self.root)
        return result


class Conflict(Exception):
    """The file moved under us; the caller should reload rather than overwrite."""


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    interface: Interface            # bound in serve()
    server_version = "tisscal"

    def log_message(self, fmt, *args):     # one line per request, not three
        if not self.path.startswith("/api/"):
            return
        print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    # -------------------------------------------------------------- plumbing
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The page is read off disk on every request, so editing it needs no restart.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _run(self, fn, *args) -> None:
        """Turn every failure into something the page can show.

        A traceback on the console and a one-line message in the browser: the message is
        what gets acted on, the traceback is what gets debugged.
        """
        try:
            self._json(fn(*args))
        except Conflict as e:
            self._json({"error": str(e), "reload": True}, 409)
        except (ConfigError, WriteError, KeyError) as e:
            self._json({"error": str(e)}, 400)
        except SystemExit as e:        # load_config exits on a bad file
            self._json({"error": str(e)}, 400)
        except Exception as e:
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    # --------------------------------------------------------------- routing
    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            page = STATIC / "index.html"
            if not page.exists():
                return self._send(500, b"static/index.html is missing", "text/plain")
            return self._send(200, page.read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            return self._run(self.interface.state)
        self._json({"error": f"no such path: {self.path}"}, 404)

    def do_POST(self) -> None:
        routes = {"/api/diff": self.interface.diff,
                  "/api/save": self.interface.save,
                  "/api/commit": self.interface.commit}
        if self.path == "/api/refresh":
            return self._run(self.interface.refresh)
        if self.path in routes:
            try:
                payload = self._body()
            except json.JSONDecodeError as e:
                return self._json({"error": f"bad JSON: {e}"}, 400)
            return self._run(routes[self.path], payload)
        self._json({"error": f"no such path: {self.path}"}, 404)


def serve(root: Path, config_name: str = "settings.toml", port: int = 8765,
          open_browser: bool = True) -> None:
    interface = Interface(root, config_name)

    # Load everything once before announcing a URL: a broken settings file or a feed that
    # will not fetch should fail here, in the terminal, not as a red box in a browser.
    state = interface.state()
    # flush: the banner has to appear even when the output is piped to a log.
    print(f"{config_name}: profile {state['profile']}, {state['events']} events, "
          f"{len(state['courses'])} courses in the feed", flush=True)
    if not state["secrets"]["calendar_id"]:
        print("  note: no calendar id is set, so nothing could be synced yet", flush=True)

    bound = type("BoundHandler", (Handler,), {"interface": interface})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), bound)
    url = f"http://127.0.0.1:{port}/"
    print(f"\n  {url}   (Ctrl-C to stop)\n", flush=True)
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
