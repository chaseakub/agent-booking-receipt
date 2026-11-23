# Booking Receipt

**Did the calendar save the booking before the agent timed out?** Booking Receipt gives a calendar hold a stable ID. A restarted agent can read the calendar, recover the original receipt, and leave the existing event alone. Free, MIT licensed, and usable without a hosted account.

[Run the public verification](https://github.com/chaseakub/agent-booking-receipt/actions/workflows/verify.yml) · [Instructions for agents](skills/booking-receipt/SKILL.md) · [Using it regularly?](#using-it-regularly)

## Try the failure in one command

With [uv](https://github.com/astral-sh/uv) installed, run:

```sh
uvx --from 'agent-booking-receipt[demo] @ git+https://github.com/chaseakub/agent-booking-receipt.git@v0.1.1' booking-receipt demo
```

The demo starts an isolated Radicale calendar on loopback and creates a hold. It drops the HTTP reply after the server commits and makes the first confirmation read unavailable. A fresh process recovers the event. A third process repeats the original request. The demo independently lists the calendar and checks that it contains one event and that only one create request reached the server. The calendar and test credentials are removed on exit.

## Install for an agent

```sh
npx skills add chaseakub/agent-booking-receipt --skill booking-receipt
```

The skill helps an agent decide whether to create a hold or check an uncertain result. Install the CLI with Python 3.10 or newer.

```sh
uv tool install 'git+https://github.com/chaseakub/agent-booking-receipt.git@v0.1.1'
```

## Use your calendar

For CalDAV, set `BOOKING_CALDAV_URL` to a calendar collection and supply `BOOKING_CALDAV_USER` and `BOOKING_CALDAV_PASSWORD` when required. Remote connections require HTTPS. For Google Calendar, set `BOOKING_GOOGLE_CALENDAR_ID` and `BOOKING_GOOGLE_ACCESS_TOKEN` to an authorized calendar and a current token with calendar event access. Select one provider per invocation.

Supply a stable tenant namespace and a business key that already exists before the first write, such as a service order and its follow-up purpose. Pass the same calendar and original booking data on recovery. See the [input file](examples/booking.json).

```sh
booking-receipt ensure --file booking.json
booking-receipt recover --file booking.json
```

`ensure` reads first, attempts at most one create, and then reads back. `recover` only reads. Both return JSON. Exit 0 means confirmed, 2 indicates another booking state, and 1 means invalid configuration or input.

| State | What it means |
| --- | --- |
| confirmed | The calendar returned the expected event ID and request fingerprint. |
| unknown | The provider could not establish the outcome. Keep the same key and use read-only recovery. |
| missing | A read-only lookup found no visible record. A previous create may have been delayed or the event may have been deleted. |
| conflict | The address contains a different event or the original request differs. The existing event is preserved. |
| cancelled | The provider returned a cancellation or tombstone. The adapter stops. |
| rejected | The provider explicitly rejected this create attempt. Inspect the reported error. |

CalDAV creation uses a deterministic resource URL and `If-None-Match: *`. Google creation uses a client-assigned event ID and a private request fingerprint. Google documents limits to collision detection across distributed writes. A receipt confirms that the original request marker exists. People may edit the event after creation. A CalDAV server may remove deleted objects without retaining tombstones. Use `recover` after an uncertain write, including when a later deletion is possible. This version creates single timed calendar holds with no attendees. It does not reserve availability, reschedule meetings or deliver invitations. [Google Calendar event IDs](https://developers.google.com/workspace/calendar/api/guides/create-events) · [CalDAV](https://www.rfc-editor.org/rfc/rfc4791)

<a id="assisted-pilot"></a>

## Using it regularly?

Booking Receipt is free under the MIT license. If you personally rely on it for recurring bookings, tell us what it handles and how often. A sentence is enough. Sharing is optional.

[Share recurring use on GitHub](https://github.com/chaseakub/agent-booking-receipt/issues/new?template=recurring-use.yml) · [Tell Replio privately](mailto:chase@repliohq.com?subject=How%20I%20use%20Booking%20Receipt)

## Design precedents

[Once](https://github.com/frogscooper/once) stores tool-call outcomes behind a decorator. [Inngest](https://www.inngest.com/blog/the-queue-is-durable-your-worker-is-not) explains why a retried workflow step can repeat an external effect. Booking Receipt uses the calendar itself as the record for a specific business action. An [MCP scheduling developer’s report](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/3394#issuecomment-6244260292) illustrates why ambiguous replies matter.
