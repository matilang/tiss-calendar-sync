@echo off
REM Daily sync for Windows Task Scheduler.
REM
REM Re-reads the TISS feed and the scraped course pages, so it picks up changes on its
REM own: a course you have just been admitted to, the exercise group you registered for,
REM a room change, a cancelled lecture. Safe to run as often as you like - events are
REM updated in place, never duplicated.
REM
REM Register it (one line, from this folder):
REM   schtasks /create /tn "TISS calendar sync" /tr "\"%~f0\"" /sc daily /st 07:00
REM Remove it again:
REM   schtasks /delete /tn "TISS calendar sync" /f
REM Run it once by hand to check:
REM   schtasks /run /tn "TISS calendar sync"

cd /d "%~dp0"

echo [%date% %time%] starting sync>> sync.log

REM TISS lectures, exams and registration deadlines
".venv\Scripts\python.exe" tiss_sync.py sync>> sync.log 2>&1
if errorlevel 1 echo [%date% %time%] TISS sync FAILED with code %errorlevel%>> sync.log

REM TUWEL deadlines - only runs once tuwel.toml exists
if exist "tuwel.toml" (
    ".venv\Scripts\python.exe" tiss_sync.py sync -c tuwel.toml>> sync.log 2>&1
    if errorlevel 1 echo [%date% %time%] TUWEL sync FAILED with code %errorlevel%>> sync.log
)

echo [%date% %time%] done>> sync.log
