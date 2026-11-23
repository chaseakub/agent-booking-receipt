"""Calendar create and read adapters. A create response alone is never a receipt."""

import ipaddress
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import requests
from icalendar import Calendar, Event


class ProviderError(Exception):
    """The provider or its configuration rejected the operation."""


class ProviderUncertain(ProviderError):
    """A later read may establish the outcome. Never assume a write failed."""


class ProviderConflict(ProviderError):
    """The resource at the expected address has a different identity."""


def _event_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{5,1024}", value):
        raise ProviderError("Expected a lowercase hexadecimal calendar event ID.")
    return value


def _request(session, method, url, **kwargs):
    try:
        return session.request(method, url, allow_redirects=False, timeout=(5, 15), **kwargs)
    except requests.RequestException:
        raise ProviderUncertain("Calendar request did not return a reliable response.") from None


def _expect(response, allowed):
    code = response.status_code
    if code in allowed:
        return
    if code in {202, 408, 423, 425, 429} or 500 <= code <= 599:
        raise ProviderUncertain(f"Calendar service returned HTTP {code}.")
    raise ProviderError(f"Calendar service rejected the request (HTTP {code}).")


def _record(event_id, marker, status, url):
    return {"id": event_id, "fingerprint": marker, "status": status, "url": url}


def _collection_url(url):
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        parts.port  # Validate an explicitly supplied port.
        valid = (hostname and parts.scheme in {"http", "https"} and
                 parts.username is None and parts.password is None and
                 not parts.query and not parts.fragment and not re.search(r"[\s\\?#]", url))
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ProviderError("Use an HTTP(S) CalDAV collection URL without credentials, query, or fragment.")
    if parts.scheme == "http":
        try:
            local = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            local = hostname.rstrip(".").lower() == "localhost"
        if not local:
            raise ProviderError("Remote CalDAV connections require HTTPS.")
    return url.rstrip("/") + "/"


def _utc_datetime(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        raise ProviderError("Use UTC RFC 3339 event times.") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None) or parsed.microsecond:
        raise ProviderError("CalDAV requires UTC event times with whole-second precision.")
    return parsed


class CalDAV:
    """Create a single calendar object at a deterministic resource address."""

    def __init__(self, collection_url, user=None, password=None, *, session=None):
        self.collection_url = _collection_url(collection_url)
        if (user is None) != (password is None):
            raise ProviderError("Supply both the CalDAV username and password.")
        self.auth = (user, password) if user is not None else None
        self.session = requests.Session() if session is None else session

    def _url(self, event_id):
        return f"{self.collection_url}{_event_id(event_id)}.ics"

    def get(self, event_id):
        url = self._url(event_id)
        response = _request(self.session, "GET", url, auth=self.auth,
                            headers={"Accept": "text/calendar"})
        if response.status_code == 404:
            return None
        if response.status_code == 410:
            return _record(event_id, "", "cancelled", url)
        _expect(response, {200})
        try:
            calendar = Calendar.from_ical(response.content)
            events = calendar.walk("VEVENT")
        except (ValueError, TypeError, UnicodeError, AttributeError, IndexError):
            raise ProviderUncertain("CalDAV returned unreadable calendar data.") from None
        if not events or any(str(item.get("UID", "")) != event_id for item in events):
            raise ProviderConflict("CalDAV returned a different event at the booking address.")
        masters = [item for item in events if "RECURRENCE-ID" not in item]
        if len(masters) != 1:
            raise ProviderUncertain("CalDAV returned no unique primary event.")
        event = masters[0]
        status = str(event.get("STATUS", "")).upper()
        if status not in {"", "TENTATIVE", "CONFIRMED", "CANCELLED"}:
            raise ProviderUncertain("CalDAV returned an unrecognized event status.")
        marker = str(event.get("X-BOOKING-RECEIPT", ""))
        return _record(event_id, marker, "cancelled" if status == "CANCELLED" else "active", url)

    def create(self, event_id, fingerprint, title, start, end):
        url = self._url(event_id)
        calendar = Calendar()
        calendar.add("prodid", "-//Replio//Booking Receipt//EN")
        calendar.add("version", "2.0")
        event = Event()
        event.add("uid", event_id)
        event.add("dtstamp", datetime.now(timezone.utc))
        event.add("dtstart", _utc_datetime(start))
        event.add("dtend", _utc_datetime(end))
        event.add("summary", title)
        event.add("X-BOOKING-RECEIPT", fingerprint)
        calendar.add_component(event)
        response = _request(
            self.session, "PUT", url, auth=self.auth, data=calendar.to_ical(),
            headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"},
        )
        # The caller reads the resource after either creation or a failed precondition.
        _expect(response, {200, 201, 204, 412})


class GoogleCalendar:
    """Use a client-generated Google event ID and a private receipt marker."""

    def __init__(self, calendar_id, token, *, session=None):
        if not isinstance(calendar_id, str) or not calendar_id.strip() or calendar_id in {".", ".."}:
            raise ProviderError("Supply a Google Calendar ID.")
        if not isinstance(token, str) or not token or re.search(r"\s", token):
            raise ProviderError("Supply a valid Google Calendar access token.")
        self.events_url = "https://www.googleapis.com/calendar/v3/calendars/" + quote(calendar_id, safe="") + "/events"
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self.session = requests.Session() if session is None else session

    def get(self, event_id):
        url = f"{self.events_url}/{_event_id(event_id)}"
        response = _request(self.session, "GET", url, headers=self.headers)
        if response.status_code == 404:
            return None
        if response.status_code == 410:
            # This is events.get for a specific ID. No collection sync token is sent.
            return _record(event_id, "", "cancelled", url)
        _expect(response, {200})
        try:
            event = response.json()
        except ValueError:
            raise ProviderUncertain("Google Calendar returned unreadable event data.") from None
        if not isinstance(event, dict) or not event.get("id"):
            raise ProviderUncertain("Google Calendar returned an incomplete event.")
        if event["id"] != event_id:
            raise ProviderConflict("Google Calendar returned a different event ID.")
        status = event.get("status")
        if status not in ("confirmed", "tentative", "cancelled"):
            raise ProviderUncertain("Google Calendar returned an unrecognized event status.")
        props = event.get("extendedProperties") or {}
        private = props.get("private") if isinstance(props, dict) else None
        marker = private.get("bookingReceipt") if isinstance(private, dict) else None
        return _record(event_id, marker if isinstance(marker, str) else "",
                       "cancelled" if status == "cancelled" else "active", url)

    def create(self, event_id, fingerprint, title, start, end):
        event = {
            "id": _event_id(event_id), "summary": title,
            "start": {"dateTime": start}, "end": {"dateTime": end},
            "extendedProperties": {"private": {"bookingReceipt": fingerprint}},
        }
        response = _request(self.session, "POST", self.events_url, headers=self.headers, json=event)
        # A 409 already-exists response still requires matching the remote marker.
        _expect(response, {200, 201, 409})
