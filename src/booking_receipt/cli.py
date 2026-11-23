import argparse
import json
import os
import sys
from importlib.resources import files

from .core import Booking, ensure, recover
from .providers import CalDAV, GoogleCalendar, ProviderError


def provider_from_env():
    url = os.getenv("BOOKING_CALDAV_URL")
    calendar = os.getenv("BOOKING_GOOGLE_CALENDAR_ID")
    if bool(url) == bool(calendar):
        raise ValueError("Set exactly one of BOOKING_CALDAV_URL or BOOKING_GOOGLE_CALENDAR_ID.")
    if url:
        return CalDAV(url, os.getenv("BOOKING_CALDAV_USER"), os.getenv("BOOKING_CALDAV_PASSWORD"))
    return GoogleCalendar(calendar, os.getenv("BOOKING_GOOGLE_ACCESS_TOKEN"))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="booking-receipt", description="Create or recover a calendar hold.")
    commands = parser.add_subparsers(dest="action", required=True)
    for verb in ("ensure", "recover"):
        cmd = commands.add_parser(verb)
        cmd.add_argument("--file", required=True, help="Booking JSON file, or - to read stdin.")
    commands.add_parser("demo", help="Run the lost-response demo on a temporary local Radicale calendar.")
    commands.add_parser("offer", help="Return the human purchase handoff as JSON.")
    args = parser.parse_args(argv)
    try:
        if args.action == "offer":
            result = json.loads(files("booking_receipt").joinpath("offer.json").read_text(encoding="utf-8"))
        elif args.action == "demo":
            from .demo import run_demo
            result = run_demo()
        else:
            if args.file == "-":
                data = json.load(sys.stdin)
            else:
                with open(args.file, encoding="utf-8") as stream:
                    data = json.load(stream)
            expected = {"namespace", "key", "title", "start", "end"}
            if not isinstance(data, dict) or data.keys() != expected:
                raise ValueError("Expected exactly namespace, key, title, start and end.")
            booking = Booking(**data)
            operation = ensure if args.action == "ensure" else recover
            result = operation(provider_from_env(), booking)
    except (ValueError, OSError, ProviderError, RuntimeError) as exc:
        print(json.dumps({"state": "error", "detail": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    if args.action in ("ensure", "recover") and result["state"] != "confirmed":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
