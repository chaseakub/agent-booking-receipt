"""Replay a lost booking response against a real, temporary Radicale calendar."""

import json
import os
import secrets
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from wsgiref.simple_server import WSGIRequestHandler, make_server
from xml.etree import ElementTree

import requests
from icalendar import Calendar


class _QuietWSGI(WSGIRequestHandler):
    def log_message(self, *args):
        pass


@contextmanager
def _serving(server):
    # Both server constructors bind an OS-assigned port and listen before returning.
    with server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


def _fault_proxy(backend, counters, client):
    class FaultProxy(BaseHTTPRequestHandler):
        def relay(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            headers = {key: self.headers[key] for key in
                       ("Authorization", "Content-Type", "If-None-Match", "Accept") if key in self.headers}
            if self.command == "GET" and counters["dropped_put_replies"] and not counters["unavailable_reads"]:
                counters["unavailable_reads"] += 1
                self.send_error(503)
                return
            target = backend + urlsplit(self.path).path
            if self.command == "PUT":
                counters["provider_puts"] += 1
            result = client.request(self.command, target, headers=headers, data=body,
                                    timeout=10, allow_redirects=False)
            if (self.command == "PUT" and result.status_code in (200, 201, 204)
                    and not counters["dropped_put_replies"]):
                counters["dropped_put_replies"] += 1
                counters["committed_put_http_status"] = result.status_code
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            self.send_response(result.status_code)
            self.send_header("Content-Length", str(len(result.content)))
            if "Content-Type" in result.headers:
                self.send_header("Content-Type", result.headers["Content-Type"])
            self.end_headers()
            self.wfile.write(result.content)

        do_GET = relay
        do_PUT = relay

        def log_message(self, *args):
            pass

    return HTTPServer(("127.0.0.1", 0), FaultProxy)


def _observe_calendar(client, url, auth):
    # Count individual VEVENTs, independent of the receipt returned by our CLI.
    response = client.request(
        "PROPFIND", url, auth=auth, headers={"Depth": "1", "Content-Type": "application/xml"},
        data=b'<propfind xmlns="DAV:"><prop><resourcetype/></prop></propfind>', timeout=10,
    )
    if response.status_code != 207:
        raise RuntimeError(f"Provider listing failed (HTTP {response.status_code}).")
    base = urlsplit(url)
    uids = []
    for href in ElementTree.fromstring(response.content).findall("{DAV:}response/{DAV:}href"):
        path = urlsplit(href.text or "").path
        if not path.endswith(".ics"):
            continue
        resource = client.get(f"{base.scheme}://{base.netloc}{path}", auth=auth, timeout=10)
        if resource.status_code != 200:
            raise RuntimeError(f"Provider event read failed (HTTP {resource.status_code}).")
        events = Calendar.from_ical(resource.content).walk("VEVENT")
        for event in events:
            if event.get("ATTENDEE"):
                raise RuntimeError("Demo calendar unexpectedly contains an attendee.")
            uids.append(str(event["UID"]))
    return uids


def _call(action, path, env, expected_code, expected_state):
    process = subprocess.run(
        [sys.executable, "-m", "booking_receipt.cli", action, "--file", str(path)],
        env=env, capture_output=True, text=True, timeout=30,
    )
    if process.returncode != expected_code:
        raise RuntimeError(f"Demo {action} returned exit {process.returncode}, expected {expected_code}.")
    receipt = json.loads(process.stdout)
    if receipt["state"] != expected_state:
        raise RuntimeError(f"Demo {action} did not report {expected_state}.")
    return receipt


def run_demo():
    try:
        from radicale import Application, config
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install the demo extra to run the local Radicale calendar.") from exc

    with TemporaryDirectory(prefix="booking-receipt-") as temp, requests.Session() as client:
        directory = Path(temp)
        client.trust_env = False
        user, password = "demo", secrets.token_urlsafe(24)
        users = directory / "users"
        users.write_text(f"{user}:{password}\n", encoding="utf-8")
        settings = config.load([])
        settings.update({
            "auth": {"type": "htpasswd", "htpasswd_filename": str(users), "htpasswd_encryption": "plain"},
            "storage": {"filesystem_folder": str(directory / "calendar")},
            "rights": {"type": "owner_only"},
        }, "isolated booking demo")
        request = directory / "booking.json"
        request.write_text(json.dumps({
            "namespace": "demo", "key": "meeting-101", "title": "Booking receipt demo",
            "start": "2030-04-15T15:00:00Z", "end": "2030-04-15T15:30:00Z",
        }), encoding="utf-8")
        dav = make_server("127.0.0.1", 0, Application(settings), handler_class=_QuietWSGI)
        stats = {"provider_puts": 0, "dropped_put_replies": 0, "unavailable_reads": 0}
        with _serving(dav) as backend:
            calendar_path = "/demo/holds/"
            result = client.request("MKCALENDAR", backend + calendar_path, auth=(user, password), timeout=10)
            if result.status_code != 201:
                raise RuntimeError(f"Temporary calendar creation failed (HTTP {result.status_code}).")
            with _serving(_fault_proxy(backend, stats, client)) as proxy:
                env = {k: v for k, v in os.environ.items() if not k.startswith("BOOKING_")}
                env.update(BOOKING_CALDAV_URL=proxy + calendar_path, BOOKING_CALDAV_USER=user,
                           BOOKING_CALDAV_PASSWORD=password, NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
                first = _call("ensure", request, env, 2, "unknown")
                recovered = _call("recover", request, env, 0, "confirmed")
                replay = _call("ensure", request, env, 0, "confirmed")
            uids = _observe_calendar(client, backend + calendar_path, (user, password))
        event_id = recovered["event_id"]
        if (uids != [event_id] or first["event_id"] != event_id or replay["event_id"] != event_id
                or stats["provider_puts"] != 1 or stats["dropped_put_replies"] != 1
                or stats["unavailable_reads"] != 1):
            raise RuntimeError("Calendar observations did not match the three CLI runs.")
        return {
            "provider": "Radicale", "first_attempt": first["state"], "recovery": recovered["state"],
            "replay": replay["state"], "provider_events": len(uids), "event_id": event_id, **stats,
        }
