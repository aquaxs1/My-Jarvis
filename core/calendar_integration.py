"""
JARVIS Google Calendar Integration
- OAuth2-Authentifizierung
- Termine lesen und erstellen
- Natürlichsprachliche Zeitangaben parsen
"""
import os
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

JARVIS_DIR = Path.home() / ".jarvis"
TOKEN_PATH = JARVIS_DIR / "google_token.json"
CREDENTIALS_PATH = JARVIS_DIR / "google_credentials.json"
SCOPES = ["https://www.googleapis.com/auth/calendar"]


class CalendarManager:
    def __init__(self, config: dict):
        self.config = config
        self._service = None
        self._available = False
        self._init()

    def _init(self):
        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
            self._available = True
        except ImportError:
            logger.info("[Calendar] google-api-python-client nicht installiert")
            return

        self._authenticate()

    def _authenticate(self):
        if not self._available:
            return

        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds = None
        if TOKEN_PATH.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
            except Exception as e:
                logger.warning("[Calendar] Token laden fehlgeschlagen: %s", e)

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.warning("[Calendar] Token refresh fehlgeschlagen: %s", e)
                creds = None

        if not creds or not creds.valid:
            if not CREDENTIALS_PATH.exists():
                logger.info("[Calendar] google_credentials.json fehlt in %s", JARVIS_DIR)
                return
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CREDENTIALS_PATH), SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                logger.error("[Calendar] OAuth fehlgeschlagen: %s", e)
                return

        JARVIS_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json())

        try:
            self._service = build("calendar", "v3", credentials=creds)
            logger.info("[Calendar] Google Calendar verbunden")
        except Exception as e:
            logger.error("[Calendar] Service-Erstellung fehlgeschlagen: %s", e)

    @property
    def is_configured(self) -> bool:
        return self._service is not None

    def get_events(self, days: int = 1) -> list:
        if not self._service:
            return []

        now = datetime.utcnow()
        time_min = now.isoformat() + "Z"
        time_max = (now + timedelta(days=days)).isoformat() + "Z"

        try:
            result = self._service.events().list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                maxResults=20,
                singleEvents=True,
                orderBy="startTime"
            ).execute()

            events = []
            for event in result.get("items", []):
                start = event["start"].get("dateTime", event["start"].get("date", ""))
                end = event["end"].get("dateTime", event["end"].get("date", ""))
                events.append({
                    "id": event.get("id", ""),
                    "title": event.get("summary", "(Kein Titel)"),
                    "start": start,
                    "end": end,
                    "location": event.get("location", ""),
                    "description": event.get("description", ""),
                })
            return events
        except Exception as e:
            logger.error("[Calendar] Events abrufen fehlgeschlagen: %s", e)
            return []

    def create_event(self, title: str, start_dt: datetime,
                     duration_minutes: int = 60,
                     description: str = "") -> Optional[dict]:
        if not self._service:
            return None

        end_dt = start_dt + timedelta(minutes=duration_minutes)
        event_body = {
            "summary": title,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/Vienna"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "Europe/Vienna"},
        }
        if description:
            event_body["description"] = description

        try:
            event = self._service.events().insert(
                calendarId="primary", body=event_body
            ).execute()
            logger.info("[Calendar] Termin erstellt: %s", title)
            return {
                "id": event.get("id"),
                "title": title,
                "start": start_dt.isoformat(),
                "link": event.get("htmlLink", ""),
            }
        except Exception as e:
            logger.error("[Calendar] Termin erstellen fehlgeschlagen: %s", e)
            return None

    def format_events_text(self, events: list) -> str:
        if not events:
            return "Keine Termine gefunden."
        lines = []
        for ev in events:
            start = ev["start"]
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                time_str = dt.strftime("%H:%M")
                date_str = dt.strftime("%d.%m.%Y")
            except (ValueError, AttributeError):
                time_str = start
                date_str = ""
            loc = f" ({ev['location']})" if ev.get("location") else ""
            lines.append(f"- **{time_str}** {ev['title']}{loc}")
        return "\n".join(lines)


def parse_datetime_natural(text: str) -> Optional[datetime]:
    try:
        import dateparser
        parsed = dateparser.parse(
            text,
            languages=["de", "en"],
            settings={
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": datetime.now(),
            }
        )
        return parsed
    except ImportError:
        logger.warning("[Calendar] dateparser nicht installiert, versuche einfaches Parsing")
    except Exception as e:
        logger.debug("[Calendar] dateparser fehlgeschlagen: %s", e)

    return _simple_parse(text)


def _simple_parse(text: str) -> Optional[datetime]:
    import re
    now = datetime.now()
    text_lower = text.lower().strip()

    if "morgen" in text_lower:
        base = now + timedelta(days=1)
    elif "übermorgen" in text_lower:
        base = now + timedelta(days=2)
    elif "heute" in text_lower:
        base = now
    else:
        base = now

    time_match = re.search(r'(\d{1,2})[:\.](\d{2})', text)
    if time_match:
        h, m = int(time_match.group(1)), int(time_match.group(2))
        return base.replace(hour=h, minute=m, second=0, microsecond=0)

    time_match = re.search(r'(\d{1,2})\s*uhr', text_lower)
    if time_match:
        h = int(time_match.group(1))
        return base.replace(hour=h, minute=0, second=0, microsecond=0)

    return base.replace(second=0, microsecond=0)
