"""Google Calendar credentials.

Two ways in, tried in this order:

1. `service_account.json` - a service account key. Preferred for anything unattended.
   No browser, no consent screen, and no token that expires: the key is the credential.
   The service account only sees calendars you explicitly share with its address, which
   is exactly what we want - it can touch the TU Wien calendar and nothing else.

2. `credentials.json` + `token.json` - the normal "log in as yourself" OAuth flow.
   Opens a browser once. Note that while the OAuth app's publishing status is "Testing",
   Google expires the refresh token after 7 days, so a scheduled sync breaks weekly.
   Publishing the app would fix that, but Google now demands a branding page with a
   homepage and privacy policy on a domain you own - hence option 1.
"""
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def get_service(base: Path):
    key_file = base / "service_account.json"
    if key_file.exists():
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(
            str(key_file), scopes=SCOPES)
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    token_file = base / "token.json"
    creds_file = base / "credentials.json"
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_file.exists():
                raise SystemExit("credentials.json missing - see README step 3.")
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def whoami(base: Path) -> str:
    """Which identity will be used - handy when a share is missing and nothing syncs."""
    key_file = base / "service_account.json"
    if key_file.exists():
        import json

        return json.loads(key_file.read_text(encoding="utf-8")).get("client_email", "?")
    return "OAuth user (token.json)"
