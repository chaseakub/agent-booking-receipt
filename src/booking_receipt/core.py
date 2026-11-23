import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from .providers import ProviderConflict, ProviderError, ProviderUncertain

# Public and fixed across versions. Changing this breaks recovery of existing bookings.
BOOKING_NAMESPACE = uuid.UUID("09b89996-6c12-48a8-a47e-286340319367")
RFC3339 = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:Z|[+-]\d\d:\d\d)")


def _encode(parts):
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def _time(value):
    if not isinstance(value, str) or not RFC3339.fullmatch(value):
        raise ValueError("Start and end must be RFC 3339 timestamps with a timezone and whole seconds.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Booking:
    namespace: str
    key: str
    title: str
    start: str
    end: str

    def __post_init__(self):
        for field in ("namespace", "key", "title"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
                raise ValueError(f"Supply a nonempty {field} without control characters.")
        object.__setattr__(self, "start", _time(self.start))
        object.__setattr__(self, "end", _time(self.end))
        if self.end <= self.start:
            raise ValueError("End must be later than start.")

    @property
    def event_id(self):
        return uuid.uuid5(BOOKING_NAMESPACE, _encode([self.namespace, self.key])).hex

    @property
    def fingerprint(self):
        payload = _encode(["booking-v1", self.title, self.start, self.end])
        return hashlib.sha256(payload.encode()).hexdigest()


def _receipt(booking, state, detail, url=None):
    result = {"state": state, "event_id": booking.event_id, "detail": detail}
    if url:
        result["resource_url"] = url
    return result


def recover(provider, booking):
    """Read only. A missing event does not prove a previous create never happened."""
    try:
        record = provider.get(booking.event_id)
    except ProviderConflict as exc:
        return _receipt(booking, "conflict", str(exc))
    except ProviderError as exc:
        return _receipt(booking, "unknown", str(exc))
    if record is None:
        return _receipt(booking, "missing", "No event visible at this address.")
    if record["status"] == "cancelled":
        return _receipt(booking, "cancelled", "Calendar reports a cancellation or tombstone.")
    if record["id"] != booking.event_id or record["fingerprint"] != booking.fingerprint:
        return _receipt(booking, "conflict", "Existing event does not match this booking request.")
    return _receipt(booking, "confirmed", "Calendar returned the matching booking marker.", record["url"])


def ensure(provider, booking):
    """Find the existing hold or attempt one provider-native create, then read it back."""
    initial = recover(provider, booking)
    if initial["state"] != "missing":
        return initial
    try:
        provider.create(booking.event_id, booking.fingerprint, booking.title, booking.start, booking.end)
    except ProviderConflict as exc:
        return _receipt(booking, "conflict", str(exc))
    except ProviderUncertain:
        pass  # The provider may have saved it. Only a read can establish that.
    except ProviderError as exc:
        return _receipt(booking, "rejected", str(exc))
    receipt = recover(provider, booking)
    if receipt["state"] == "missing":
        return _receipt(booking, "unknown", "Create was attempted, but no event is visible yet. Use recover.")
    return receipt
