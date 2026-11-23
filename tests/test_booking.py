import unittest
from dataclasses import replace
from unittest.mock import Mock

from booking_receipt import Booking, ensure, recover
from booking_receipt.providers import CalDAV, GoogleCalendar, ProviderError, ProviderUncertain

REQUEST = Booking("workshop", "order-240:inspection", "Inspection", "2027-04-04T14:00:00Z", "2027-04-04T15:00:00Z")


def record(booking=REQUEST):
    return {"id": booking.event_id, "fingerprint": booking.fingerprint, "status": "active", "url": "https://calendar.test/record"}


class Receipts(unittest.TestCase):
    def test_edited_intent_conflicts_without_a_second_write(self):
        provider = Mock()
        provider.get.return_value = record()
        changed = replace(REQUEST, title="Other visit")
        self.assertEqual(changed.event_id, REQUEST.event_id)
        self.assertEqual(ensure(provider, changed)["state"], "conflict")
        provider.create.assert_not_called()

    def test_equivalent_timezones_and_business_scope(self):
        offset = replace(REQUEST, start="2027-04-04T09:00:00-05:00")
        self.assertEqual(REQUEST.fingerprint, offset.fingerprint)
        self.assertNotEqual(REQUEST.event_id, replace(REQUEST, namespace="another team").event_id)
        with self.assertRaises(ValueError):
            replace(REQUEST, end="2027-04-04T13:59:59Z")

    def test_cancelled_is_terminal_even_if_marker_was_cleared(self):
        provider = Mock()
        provider.get.return_value = {**record(), "status": "cancelled", "fingerprint": ""}
        self.assertEqual(ensure(provider, REQUEST)["state"], "cancelled")
        provider.create.assert_not_called()

    def test_unreadable_preflight_never_writes(self):
        provider = Mock()
        provider.get.side_effect = ProviderUncertain("unavailable")
        self.assertEqual(ensure(provider, REQUEST)["state"], "unknown")
        provider.create.assert_not_called()

    def test_missing_after_lost_response_stays_unknown(self):
        provider = Mock()
        provider.get.return_value = None
        provider.create.side_effect = ProviderUncertain("lost")
        self.assertEqual(ensure(provider, REQUEST)["state"], "unknown")
        self.assertEqual(recover(provider, REQUEST)["state"], "missing")
        self.assertEqual(provider.create.call_count, 1)

    def test_read_only_recover_and_conflicting_ownership(self):
        provider = Mock()
        provider.get.return_value = {**record(), "fingerprint": ""}
        self.assertEqual(recover(provider, REQUEST)["state"], "conflict")
        provider.create.assert_not_called()


class Adapters(unittest.TestCase):
    @staticmethod
    def response(status, data=None):
        response = Mock(status_code=status)
        response.json.return_value = data
        return response

    def test_google_concurrent_conflict_requires_matching_readback(self):
        http = Mock()
        http.request.side_effect = [
            self.response(404), self.response(409),
            self.response(200, {"id": REQUEST.event_id, "status": "confirmed", "extendedProperties": {"private": {"bookingReceipt": REQUEST.fingerprint}}}),
        ]
        google = GoogleCalendar("team@example.org", "test-token", session=http)
        self.assertEqual(ensure(google, REQUEST)["state"], "confirmed")
        self.assertEqual([c.args[0] for c in http.request.call_args_list], ["GET", "POST", "GET"])
        body = http.request.call_args_list[1].kwargs["json"]
        self.assertEqual(body["id"], REQUEST.event_id)
        self.assertEqual(body["extendedProperties"]["private"]["bookingReceipt"], REQUEST.fingerprint)
        self.assertNotIn("attendees", body)
        self.assertFalse(http.request.call_args_list[1].kwargs["allow_redirects"])

    def test_google_error_status_cannot_confirm(self):
        http = Mock()
        http.request.side_effect = [self.response(404), self.response(403)]
        google = GoogleCalendar("primary", "test-token", session=http)
        self.assertEqual(ensure(google, REQUEST)["state"], "rejected")

    def test_caldav_does_not_overwrite_after_race(self):
        http = Mock()
        http.request.side_effect = [self.response(404), self.response(412), self.response(404)]
        calendar = CalDAV("https://cal.example.org/team", session=http)
        self.assertEqual(ensure(calendar, REQUEST)["state"], "unknown")
        write = http.request.call_args_list[1]
        self.assertEqual(write.args[0], "PUT")
        self.assertEqual(write.kwargs["headers"]["If-None-Match"], "*")

    def test_caldav_remote_auth_requires_tls(self):
        with self.assertRaises(ProviderError):
            CalDAV("http://calendar.example.org/path", "user", "pw")
