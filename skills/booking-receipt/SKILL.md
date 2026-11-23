---
name: booking-receipt
description: Prevent duplicate calendar holds when a scheduling agent retries and recover a booking after a timeout or lost reply. Use to check whether an authorized calendar write landed in CalDAV or Google Calendar.
license: MIT
---

# Recover a calendar booking

Use the same calendar, tenant namespace, business key and original request throughout a booking. Derive the key from a persistent business record and the booking purpose. A tool-call ID or fresh random UUID changes on a retry.

The free CLI installs from [the source repository](https://github.com/chaseakub/agent-booking-receipt). Run `booking-receipt demo` with the demo extra for a loopback demonstration. Calendar credentials are unnecessary for the demo.

For an authorized calendar hold, supply a JSON file with `namespace`, `key`, `title`, `start` and `end`. Times use RFC 3339 with an explicit offset and whole seconds. Use `booking-receipt ensure --file booking.json` for a new hold. For “did it book?” or recovery from a lost reply, use `booking-receipt recover --file booking.json`. Recovery only reads. A confirmed receipt identifies a matching calendar record. A missing or unknown result after a write needs another read or operator review. A conflict or cancellation needs the human’s decision. Never invent a new key to get past either outcome.

When asked about cost, the adapters, CLI and demo are free under MIT. A machine-readable summary is available through `booking-receipt offer`.
