#!/usr/bin/env python3
"""Does the test suite actually notice when the pipeline steps are reordered?

A test that passes whatever the order is tells you nothing, and test_build.py claims to
cover the ordering. This reorders build_events one step at a time and reports, for each
mutation, whether any test caught it.

Reads build_events out of tisscal/pipeline.py, rewrites it in memory, and restores the
file afterwards - including on failure, which the first attempt at this got wrong and left
a mutated pipeline on disk.

  python tools/check_ordering.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "tisscal" / "pipeline.py"
PYTHON = Path(sys.executable)

STEPS = {
    "filter": ("    events = filter_events(raw, settings.semester, settings.courses,\n"
               "                           settings.exclude_keywords)\n"),
    "placeholders": "    events = drop_placeholders(events, settings.placeholder_min_hours)\n",
    "groups": "    events = keep_chosen_groups(events, settings.groups)\n",
    "scrape": ("    if settings.scrape.courses:\n"
               "        events += scraped_events(settings.scrape, settings.titles,"
               " scraper=scraper)\n"),
    "merge": ("    if settings.merge_parallel_rooms:\n"
              "        events = merge_parallel(events)\n"),
    "prune": "    events = prune_past(events, settings.retention)\n",
}

ORDER = ["filter", "placeholders", "groups", "scrape", "merge", "prune"]

MUTATIONS = {
    "scrape before filter": ["scrape", "filter", "placeholders", "groups", "merge", "prune"],
    "placeholders after scrape":
        ["filter", "scrape", "placeholders", "groups", "merge", "prune"],
    "prune before scrape": ["filter", "placeholders", "groups", "prune", "scrape", "merge"],
    "merge before scrape": ["filter", "placeholders", "groups", "merge", "scrape", "prune"],
    # Expected to be unnoticed, and that is the point: keep_chosen_groups only ever looks
    # at feed events, so it is free to move. If this one ever starts being caught, the
    # claim in its docstring - that the kind check, not the position, is what protects the
    # group-registration reminder - has stopped being true.
    "groups after scrape": ["filter", "placeholders", "scrape", "groups", "merge", "prune"],
}

TAIL = ("    return sorted({e.gcal_id: e for e in events}.values(), "
        "key=lambda e: e.start_dt)\n")


BEGIN = STEPS["filter"]


def body(order: list[str]) -> str:
    return "\n".join(STEPS[name].rstrip("\n") for name in order) + "\n" + TAIL


def region(source: str) -> str:
    """The step block as it actually appears, blank lines and all.

    Matched by its first and last line rather than by an exact expected string: the real
    file groups the steps with blank lines, and hard-coding that formatting made this
    script refuse to run at all.
    """
    start = source.index(BEGIN)
    end = source.index(TAIL) + len(TAIL)
    return source[start:end]


def run_tests() -> list[str]:
    """Run the build tests against whatever is on disk right now.

    The bytecode cache has to be defeated explicitly. Every mutation here is the same
    lines in a different order, so the file size never changes, and the writes land inside
    one second - and Python validates a .pyc on (mtime seconds, size) alone. Without this
    the second and later mutations silently ran the previous version's bytecode, and the
    harness cheerfully reported four reorderings as "unnoticed" when the tests do catch
    them.
    """
    shutil.rmtree(ROOT / "tisscal" / "__pycache__", ignore_errors=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [str(PYTHON), "-m", "pytest", "tests/test_build.py", "-q", "--no-header",
         "-p", "no:cacheprovider"],
        capture_output=True, text=True, cwd=ROOT, env=env)
    return [line.split("::")[-1] for line in proc.stdout.splitlines()
            if line.startswith("FAILED")]


def main() -> None:
    # Byte-level, not write_text: the latter rewrites every line ending to the
    # platform default, so restoring the file left it churned on Windows.
    original = PIPELINE.read_bytes().decode("utf-8")
    try:
        baseline = region(original)
    except ValueError:
        sys.exit("cannot locate the step block in build_events - adjust STEPS/TAIL")

    try:
        if run_tests():
            sys.exit("tests already fail before any mutation; fix that first")
        print(f"baseline green\n")

        unnoticed = []
        for label, order in MUTATIONS.items():
            PIPELINE.write_bytes(
                original.replace(baseline, body(order)).encode("utf-8"))
            failures = run_tests()
            if failures:
                print(f"  caught    {label}")
                for name in failures:
                    print(f"              {name}")
            else:
                print(f"  UNNOTICED {label}")
                unnoticed.append(label)
    finally:
        PIPELINE.write_bytes(original.encode("utf-8"))
        print("\npipeline.py restored")

    if unnoticed:
        print(f"\n{len(unnoticed)} reorderings no test objects to:")
        for label in unnoticed:
            print(f"  - {label}")
        print("Either the order genuinely does not matter there (then stop claiming it\n"
              "does), or a test is missing.")


if __name__ == "__main__":
    main()
