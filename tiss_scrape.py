#!/usr/bin/env python3
"""
tiss_scrape.py - entry point for the course-page scraper, which now lives in
tisscal/scrape.py.

  python tiss_scrape.py 186814 194187     # exam dates, registration windows, group hours
  python tiss_scrape.py 186814 --json
  python tiss_scrape.py --config settings.toml

Equivalent to `python -m tisscal.scrape`.
"""
from tisscal.scrape import (Course, Exam, Occurrence, fetch_course, main, make_session,
                            parse_course, report, scrape)

__all__ = ["Course", "Exam", "Occurrence", "fetch_course", "main", "make_session",
           "parse_course", "report", "scrape"]

if __name__ == "__main__":
    main()
