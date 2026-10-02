#!/usr/bin/env python3
"""One number for "the calendar output did not change".

Every refactor so far has been checked by re-running the pipeline against the real feed
and comparing the result to the previous run. That check was done with a throwaway
command each time, which has an obvious flaw: the formula was not written down, so two
runs could disagree for no better reason than that the second one hashed a different
projection of the same events. Hence this file.

The hash covers what actually reaches Google - the event id and the full request body,
including title, times, location, reminders, colour and tag - so a change in any of them
moves the number, and a change in anything else does not.

    python tools/fingerprint.py                      # settings.toml
    python tools/fingerprint.py -c settings.tuwel.toml
    python tools/fingerprint.py --json out.json      # also dump the bodies, to diff

Needs the network: the point is the real feed, not a fixture. tests/ cover the fixture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tisscal.config import load_config          # noqa: E402
from tisscal.feed import fetch_feed, parse_feed  # noqa: E402
from tisscal.gcal import _gcal_body             # noqa: E402
from tisscal.pipeline import build_events       # noqa: E402


def bodies(settings, events) -> list:
    return [[e.gcal_id, _gcal_body(e, settings)] for e in events]


def fingerprint(payload: list) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", default="settings.toml")
    ap.add_argument("--json", help="write the event bodies here, for a line-by-line diff")
    args = ap.parse_args()

    path = Path(args.config)
    settings = load_config(path if path.is_absolute() else ROOT / path)
    events = build_events(parse_feed(fetch_feed(settings.ical_url)), settings)
    payload = bodies(settings, events)

    if args.json:
        Path(args.json).write_text(
            json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False, default=str),
            encoding="utf-8")

    print(f"{settings.profile:8} {len(events):4} events  fingerprint {fingerprint(payload)}")


if __name__ == "__main__":
    main()
