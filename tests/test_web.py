"""The local interface: what it shows, what it writes, and what it must never send.

Everything here runs offline. The Interface normally fetches the feed and scrapes TISS; in
these tests both are supplied directly, which is also how the HTTP layer gets exercised
without a network or a browser.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from tisscal.model import VIENNA
from tisscal.pipeline import build_events
from tisscal.plan import diff
from tisscal.settings_io import as_dict
from tisscal.web import Conflict, Handler, Interface, plan_json, survey
from conftest import make_event, shift

ROOT = Path(__file__).resolve().parent.parent

BODY = {"summary": "VU Algorithmics", "location": "HS 6",
        "start": {"dateTime": "2026-11-10T10:00:00+01:00"}}


@pytest.fixture
def project(tmp_path):
    """A settings file with its secrets beside it, as a real working copy has."""
    (tmp_path / "settings.toml").write_bytes((ROOT / "settings.toml").read_bytes())
    (tmp_path / ".secrets.toml").write_text(
        'calendar_id = "test@group.calendar.google.com"\n'
        '[config]\nical_url = "https://tiss.invalid/feed?token=not-a-real-token"\n',
        encoding="utf-8")
    return tmp_path


@pytest.fixture(scope="module")
def parsed_course():
    """The saved course page, parsed once for the whole module.

    Parsing it per test made this file five times slower than the thing it models:
    Interface.scraper() wraps the scraper in plan.once(), so in production the pages are
    parsed once per process, not once per click.
    """
    import tisscal.scrape as sc

    page = Path(__file__).resolve().parent / "fixtures" / "course_186814.html"
    return [sc.parse_course(page.read_text(encoding="utf-8"), "186814", "2026W")]


@pytest.fixture
def interface(project, events, parsed_course):
    """An Interface that never touches the network: feed and scraper both supplied."""
    iface = Interface(project, "settings.toml")
    iface._feed = events
    iface._scraper = lambda numbers, semester: parsed_course
    return iface


# --------------------------------------------------------------------------- #
class TestSurvey:
    def test_a_course_in_the_feed_but_not_configured_still_appears(self, events, cfg):
        """The discovery half, and the reason this table exists: a course you were just
        admitted to is in the feed before it is in settings.toml, and nothing used to say
        so except running `list` and reading it."""
        settings = shift(cfg, courses=["186.814"])
        rows = survey(events, build_events(events, settings), settings)
        keys = {r["key"]: r for r in rows}
        assert "194.207" in keys
        assert keys["194.207"]["in_config"] is False
        assert keys["194.207"]["in_feed"] > 0
        assert keys["194.207"]["synced"] == 0

    def test_in_config_follows_the_real_filter(self, events, cfg):
        """Not a re-implementation of the course rule: a row counts as configured when its
        events actually survive the filter, so the table cannot drift from the pipeline."""
        settings = shift(cfg, courses=["186.814"])
        rows = {r["key"]: r for r in survey(events, build_events(events, settings), settings)}
        assert rows["186.814"]["in_config"] is True
        assert rows["192.194"]["in_config"] is False

    def test_scraped_events_are_counted_apart(self, events, cfg, course_html):
        """Otherwise a course reads "28 in the feed, 39 on the calendar", which looks like
        a bug rather than like exams coming off the course page."""
        import tisscal.scrape as sc

        settings = shift(cfg, courses=["186.814"], scrape={"courses": ["186814"]})
        built = build_events(events, settings,
                             scraper=lambda n, s: [sc.parse_course(course_html, "186814", s)])
        row = {r["key"]: r for r in survey(events, built, settings)}["186.814"]
        assert row["scraped"] > 0
        assert row["synced"] > row["scraped"]
        assert row["synced"] - row["scraped"] <= row["in_feed"]

    def test_next_is_the_next_one_still_to_come(self, cfg):
        past = make_event(start=datetime.now(VIENNA) - timedelta(days=5), uid="old")
        soon = make_event(start=datetime.now(VIENNA) + timedelta(days=2), uid="soon")
        later = make_event(start=datetime.now(VIENNA) + timedelta(days=9), uid="later")
        row = survey([past, soon, later], [past, soon, later], cfg)[0]
        assert row["next"].startswith(soon.start_dt.isoformat()[:10])

    def test_synced_courses_come_first(self, events, cfg):
        settings = shift(cfg, courses=["186.814"])
        rows = survey(events, build_events(events, settings), settings)
        synced = [bool(r["synced"]) for r in rows]
        assert synced == sorted(synced, reverse=True)

    def test_events_with_no_course_number_get_one_row(self, events, cfg):
        rows = {r["key"] for r in survey(events, build_events(events, cfg), cfg)}
        assert "(no course number)" in rows

    def test_the_groups_a_course_has_are_reported_with_their_counts(self, cfg):
        """What the Group dropdown is built from.

        Discovered rather than configured: the options are the groups the feed actually
        delivers, so there is no letter to know and no way to pick one that does not
        exist. The counts are what make the choice obvious - "A: 2, B: 1" says plainly
        that most of what is on the calendar is not yours.
        """
        feed = [make_event(course_nr="192.216", description=d, uid=f"u{i}")
                for i, d in enumerate(["Lecture", "Exercises Group A (Labs)",
                                       "Exercises Group A (Labs)", "Exercises Group B"])]
        row = survey(feed, feed, cfg)[0]
        assert row["groups"] == {
            "A": {"name": "Exercises Group A (Labs)", "events": 2},
            "B": {"name": "Exercises Group B", "events": 1},
        }

    def test_a_course_with_no_groups_reports_none(self, cfg):
        """Most courses. The column has to stay empty for them rather than offer a choice
        that would do nothing."""
        feed = [make_event(description="Lecture")]
        assert survey(feed, feed, cfg)[0]["groups"] == {}


class TestPlanJson:
    def test_the_shape_the_page_expects(self):
        payload = plan_json(diff({"a": BODY}, {"a": BODY | {"location": "X"}}))
        assert payload["counts"] == {"created": 0, "deleted": 0, "updated": 1, "unchanged": 0}
        assert payload["changes"][0]["fields"] == ["location"]
        assert payload["truncated"] is False

    def test_it_is_json_serialisable(self, events, cfg):
        """Bodies contain dates and times; a page that cannot parse them shows nothing."""
        built = build_events(events, cfg)
        from tisscal.plan import event_bodies

        payload = plan_json(diff({}, event_bodies(built, cfg)))
        assert json.loads(json.dumps(payload, default=str))["counts"]["created"] == len(built)

    def test_a_huge_diff_is_truncated(self):
        before = {f"id{n}": BODY for n in range(40)}
        payload = plan_json(diff(before, {}), limit=10)
        assert len(payload["changes"]) == 10
        assert payload["truncated"] is True
        assert payload["counts"]["deleted"] == 40, "the counts still describe all of it"


class TestInterface:
    def test_state_never_carries_the_secret_values(self, interface):
        """A browser tab leaks through history, extensions and screenshots. The page is
        told whether a secret is set, never what it is."""
        blob = json.dumps(interface.state(), default=str)
        assert "not-a-real-token" not in blob
        assert "test@group.calendar.google.com" not in blob
        assert interface.state()["secrets"] == {"ical_url": True, "calendar_id": True}

    def test_state_reports_the_file_and_profile(self, interface):
        state = interface.state()
        assert state["file"] == "settings.toml"
        assert state["profile"] == "config"
        assert state["events"] > 0
        assert state["settings"]["courses"]

    def test_an_unchanged_draft_diffs_to_nothing(self, interface):
        payload = interface.diff({"settings": interface.state()["settings"]})
        assert payload["vs_file"]["counts"]["created"] == 0
        assert payload["vs_file"]["counts"]["deleted"] == 0
        assert payload["vs_file"]["counts"]["updated"] == 0

    def test_a_draft_change_shows_up_as_a_difference(self, interface):
        draft = interface.state()["settings"]
        draft["courses"] = ["186.814"]
        payload = interface.diff({"settings": draft})
        assert payload["vs_file"]["counts"]["deleted"] > 0

    def test_a_typo_in_the_draft_is_rejected(self, interface):
        from tisscal.config import ConfigError

        draft = interface.state()["settings"]
        draft["placeholder_min_hourz"] = 4
        with pytest.raises(ConfigError, match="did you mean"):
            interface.diff({"settings": draft})

    def test_saving_writes_the_file_and_says_what_changed(self, interface):
        state = interface.state()
        draft = state["settings"]
        draft["merge_parallel_rooms"] = False
        out = interface.save({"settings": draft, "mtime": state["mtime"]})
        assert out["changed"]["merge_parallel_rooms"] == [True, False]
        assert interface.settings().merge_parallel_rooms is False

    def test_saving_keeps_the_comments(self, interface):
        text = interface.path.read_text(encoding="utf-8")
        before = sum(1 for l in text.splitlines() if l.strip().startswith("#"))
        state = interface.state()
        draft = state["settings"]
        draft["exercises"]["color_id"] = "3"
        interface.save({"settings": draft, "mtime": state["mtime"]})
        after = sum(1 for l in interface.path.read_text(encoding="utf-8").splitlines()
                    if l.strip().startswith("#"))
        assert after == before

    def test_saving_over_someone_elses_edit_is_refused(self, interface):
        """The same file is edited in VS Code. Overwriting a save made there without a word
        would be the worst thing this page could do."""
        state = interface.state()
        interface.path.write_text(
            interface.path.read_text(encoding="utf-8") + "\n# edited elsewhere\n",
            encoding="utf-8")
        with pytest.raises(Conflict, match="changed on disk"):
            interface.save({"settings": state["settings"], "mtime": state["mtime"]})

    def test_the_refused_save_leaves_the_other_edit_alone(self, interface):
        state = interface.state()
        interface.path.write_text(
            interface.path.read_text(encoding="utf-8") + "\n# edited elsewhere\n",
            encoding="utf-8")
        with pytest.raises(Conflict):
            interface.save({"settings": state["settings"], "mtime": state["mtime"]})
        assert "# edited elsewhere" in interface.path.read_text(encoding="utf-8")

    def test_saving_nothing_is_not_an_error(self, interface):
        state = interface.state()
        out = interface.save({"settings": state["settings"], "mtime": state["mtime"]})
        assert out["changed"] == {}


# --------------------------------------------------------------------------- #
@pytest.fixture
def server(interface):
    """The real HTTP layer on a free port, so routing and error mapping are covered."""
    bound = type("BoundHandler", (Handler,), {"interface": interface,
                                              "log_message": lambda *a, **k: None})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), bound)
    # poll_interval, because shutdown() waits for one: at the 0.5 s default these seven
    # tests spent three and a half seconds doing nothing but closing sockets.
    thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.02),
                              daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def fetch(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class TestHttp:
    def test_the_page_is_served(self, server):
        code, body = fetch(server + "/")
        assert code == 200
        assert b"<title>" in body and b"TU Wien" in body

    def test_state_is_json(self, server):
        code, body = fetch(server + "/api/state")
        assert code == 200
        assert json.loads(body)["profile"] == "config"

    def test_an_unknown_path_is_404_with_a_message(self, server):
        code, body = fetch(server + "/api/nonsense")
        assert code == 404
        assert "error" in json.loads(body)

    def test_malformed_json_is_400_not_a_traceback(self, server):
        req = urllib.request.Request(server + "/api/diff", data=b"{not json",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=30)
            pytest.fail("should have been rejected")
        except urllib.error.HTTPError as e:
            assert e.code == 400
            assert "bad JSON" in json.loads(e.read())["error"]

    def test_a_config_error_is_400_with_the_loader_message(self, server):
        code, body = fetch(server + "/api/diff",
                           {"settings": {"placeholder_min_hourz": 4}})
        assert code == 400
        assert "did you mean" in json.loads(body)["error"]

    def test_a_stale_save_is_409_and_asks_for_a_reload(self, server, interface):
        code, body = fetch(server + "/api/save",
                           {"settings": as_dict(interface.settings()), "mtime": 1.0})
        assert code == 409
        payload = json.loads(body)
        assert payload["reload"] is True

    def test_a_round_trip_through_http_changes_nothing(self, server, interface):
        """settings -> JSON -> browser -> JSON -> Settings has to be lossless, or every
        diff would show spurious changes the moment the page loaded."""
        _, body = fetch(server + "/api/state")
        settings = json.loads(body)["settings"]
        code, diff_body = fetch(server + "/api/diff", {"settings": settings})
        assert code == 200
        counts = json.loads(diff_body)["vs_file"]["counts"]
        assert (counts["created"], counts["deleted"], counts["updated"]) == (0, 0, 0)


class TestAnUnloadableCommittedFile:
    """Removing a config option makes every revision before the removal unparseable, and
    the loader is strict on purpose. That must cost the historical comparison only.

    This is not hypothetical: dropping `exercises.hide_for` made /api/diff return 400 and
    show the page no diff at all, and it would have healed itself on the next commit -
    the kind of bug that comes back the next time the schema changes.
    """

    def head_cannot_load(self, interface, monkeypatch):
        from tisscal import web

        monkeypatch.setattr(web.vcs, "file_at",
                            lambda root, name, ref: 'courses = []\n[exercises]\ngone = 1\n')

    def test_the_live_diff_still_comes_back(self, interface, monkeypatch):
        self.head_cannot_load(interface, monkeypatch)
        out = interface.diff({"settings": interface.state()["settings"]})
        assert "vs_file" in out
        assert out["vs_file"]["counts"]["unchanged"] > 0

    def test_it_says_why_the_comparison_is_missing(self, interface, monkeypatch):
        self.head_cannot_load(interface, monkeypatch)
        out = interface.diff({"settings": interface.state()["settings"]})
        assert "vs_head" not in out
        assert "no longer loads" in out["vs_head_error"]
        assert "gone" in out["vs_head_error"]

    def test_over_http_it_is_a_200_not_a_400(self, server, interface, monkeypatch):
        self.head_cannot_load(interface, monkeypatch)
        code, body = fetch(server + "/api/diff",
                           {"settings": as_dict(interface.settings())})
        assert code == 200
        assert "vs_head_error" in json.loads(body)


class TestAStaleServer:
    """A server left running from before a code change reads the current settings file with
    the modules it imported at startup. That is unavoidable in a long-lived process; what is
    avoidable is the error saying nothing about it.

    Exactly what happened once: exercises.color_id was added to config.py and settings.toml
    together, and a server from before refused the file with "unknown option
    [exercises].'color_id'" - correct, and no help at all.
    """

    def test_an_error_normally_stands_alone(self, server, interface):
        code, body = fetch(server + "/api/diff", {"settings": {"nonsense": 1}})
        assert code == 400
        message = json.loads(body)["error"]
        assert "nonsense" in message
        assert "restart" not in message

    def test_a_changed_module_is_named_as_the_likely_cause(self, server, monkeypatch):
        from tisscal import web

        monkeypatch.setattr(web, "stale_modules", lambda: ["config.py"])
        code, body = fetch(server + "/api/diff", {"settings": {"nonsense": 1}})
        assert code == 400
        message = json.loads(body)["error"]
        assert "nonsense" in message, "the real error still comes first"
        assert "config.py changed since" in message
        assert "restart it" in message

    def test_it_detects_a_file_newer_than_the_process(self, monkeypatch):
        from tisscal import web

        monkeypatch.setattr(web, "STARTED", 0.0)   # as if started at the epoch
        assert "config.py" in web.stale_modules()

    def test_nothing_is_stale_for_a_server_started_now(self, monkeypatch):
        import time

        from tisscal import web

        monkeypatch.setattr(web, "STARTED", time.time() + 60)
        assert web.stale_modules() == []


class TestWhatTheGridIsDrawnFrom:
    """The month grid needs the whole calendar, not only the difference - a grid showing
    seven added chips in an otherwise empty October would say nothing about where they land.

    Every row is built from the Google request body, so the grid shows the title, colour and
    times that would actually be sent. One source, no second opinion to drift from it.
    """

    def test_state_carries_every_event(self, interface):
        state = interface.state()
        assert len(state["calendar"]) == state["events"]

    def test_a_row_says_what_a_grid_needs(self, interface):
        row = interface.state()["calendar"][0]
        assert set(row) == {"id", "title", "location", "colour", "start", "end", "all_day"}
        assert row["start"] and row["title"]

    def test_a_timed_event_is_not_all_day(self, interface):
        timed = [r for r in interface.state()["calendar"] if not r["all_day"]]
        assert timed
        assert all("T" in r["start"] for r in timed)

    def test_an_all_day_event_carries_a_bare_date(self, interface):
        """The holiday markers. A grid that read them as midnight would put them in the
        wrong cell for anyone east of Vienna."""
        whole = [r for r in interface.state()["calendar"] if r["all_day"]]
        assert whole
        assert all(len(r["start"]) == 10 for r in whole)

    def test_the_exercise_colour_reaches_the_row(self, interface):
        """What the grid is for: exercises must be visibly not lectures."""
        colours = {r["colour"] for r in interface.state()["calendar"]}
        assert interface.settings().exercises.color_id in colours
        assert None in colours, "lectures keep the calendar's own colour"

    def test_event_row_and_the_diff_agree_on_shape(self, interface):
        """The page builds rows out of the before/after bodies in a diff, using the same
        fields. If the two shapes diverged, an added event would draw differently from an
        existing one - which is exactly what the grid is there to compare."""
        from tisscal.web import event_row

        draft = interface.state()["settings"]
        draft["courses"] = ["186.814"]
        changes = interface.diff({"settings": draft})["vs_file"]["changes"]
        assert changes
        body = changes[0]["before"] or changes[0]["after"]
        assert set(event_row("x", body)) == set(interface.state()["calendar"][0])
