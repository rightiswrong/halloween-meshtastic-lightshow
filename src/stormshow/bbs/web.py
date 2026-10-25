"""The guest web page and captive portal, on the standard library's HTTP server.

Phones joining the hotspot probe a well-known URL (Apple: /hotspot-detect.html,
Android: /generate_204, Windows: /connecttest.txt, ...). dnsmasq answers every DNS
name with this box, so those probes land here; answering anything but the page
itself with a redirect makes the phone open the portal window on its own.

Hardening (SECURITY-AUDIT.md):

* F-07: at most ``max_connections`` connections are served at once; extra connections
  are closed immediately instead of each getting a thread. Each guest may have only one
  summon in flight. The firewall also caps connections per guest address.
* F-10: guests are identified by the MAC address in dnsmasq's lease file when
  available (changing IP by hand no longer resets the cooldown), else by IP.
* F-20: no inline script or style, so the Content-Security-Policy is ``'self'`` only.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import urlsplit

from .service import StormBBS

log = logging.getLogger(__name__)

MAX_BODY = 256
GUEST_NAME = "a guest at the gate"
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _asset(name: str) -> str:
    return resources.files(__package__).joinpath(f"static/{name}").read_text(encoding="utf-8")


class LeaseTable:
    """Maps a guest's IP to its MAC address using dnsmasq's lease file (re-read on change)."""

    def __init__(self, path: str | None) -> None:
        self._path = path
        self._mtime = -1.0
        self._by_ip: dict[str, str] = {}
        self._lock = threading.Lock()

    def mac_for(self, ip: str) -> str | None:
        if not self._path:
            return None
        with self._lock:
            try:
                mtime = os.stat(self._path).st_mtime
            except OSError:
                return None
            if mtime != self._mtime:
                self._by_ip = self._load()
                self._mtime = mtime
            return self._by_ip.get(ip)

    def _load(self) -> dict[str, str]:
        table: dict[str, str] = {}
        try:
            with open(self._path, encoding="ascii", errors="replace") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) >= 3:  # expiry mac ip [hostname] [client-id]
                        table[parts[2]] = parts[1].lower()
        except OSError:
            pass
        return table


def make_handler(bbs: StormBBS, leases: LeaseTable | None = None) -> type[BaseHTTPRequestHandler]:
    cfg = bbs.cfg
    portal = urlsplit(cfg.portal_url)
    portal_host = portal.netloc.lower()
    page = _asset("index.html").replace("{{TITLE}}", escape(cfg.title)).encode("utf-8")
    assets = {
        "/app.js": (_asset("app.js").encode("utf-8"), "text/javascript; charset=utf-8"),
        "/app.css": (_asset("app.css").encode("utf-8"), "text/css; charset=utf-8"),
    }
    leases = leases or LeaseTable(cfg.dhcp_leases)
    in_flight: set[str] = set()
    in_flight_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "StormBBS"
        sys_version = ""
        protocol_version = "HTTP/1.1"
        timeout = 15  # seconds of socket idle before dropping a slow client

        def log_message(self, fmt: str, *args) -> None:  # route to logging, quietly
            log.debug("%s %s", self.client_address[0], fmt % args)

        # -------------------------------------------------------------- helpers
        def _send(self, code: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj: dict) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

        def _reject(self, code: int, message: str) -> None:
            # The body may be unread: close, so it isn't parsed as a second request.
            self.close_connection = True
            body = json.dumps({"status": "error", "message": message}).encode("utf-8")
            self._send(code, body, "application/json", {"Connection": "close"})

        def _redirect(self) -> None:
            body = f'<a href="{escape(cfg.portal_url)}">Summon the storm</a>'.encode()
            self._send(302, body, "text/html; charset=utf-8", {"Location": cfg.portal_url})

        def _on_portal(self) -> bool:
            # Routing for the captive portal, not a security control: clients set Host.
            return (self.headers.get("Host") or "").lower() == portal_host

        def _guest(self) -> str:
            ip = self.client_address[0]
            mac = leases.mac_for(ip)
            return f"web:{mac}" if mac else f"web:{ip}"

        # -------------------------------------------------------------- verbs
        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if not self._on_portal():
                return self._redirect()
            if path == "/":
                return self._send(200, page, "text/html; charset=utf-8")
            if path in assets:
                body, ctype = assets[path]
                return self._send(200, body, ctype)
            if path == "/api/status":
                return self._json(200, {"title": cfg.title, "who": bbs._who(), "bolts": bbs.bolts})
            return self._redirect()

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if path != "/api/summon" or not self._on_portal():
                return self._reject(404, "Not here.")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                return self._reject(400, "Bad request.")
            try:
                data = json.loads(self.rfile.read(length))
                word = data["command"]
                if not isinstance(word, str):
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                return self._reject(400, "Bad request.")

            guest = self._guest()
            with in_flight_lock:
                if guest in in_flight:
                    return self._json(429, {"status": "busy", "message": "One summon at a time, please."})
                in_flight.add(guest)
            try:
                result = bbs.summon(guest, GUEST_NAME, word)
            finally:
                with in_flight_lock:
                    in_flight.discard(guest)
            code = 429 if result.status == "cooldown" else 200
            return self._json(code, result.as_dict())

    return Handler


class PortalServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with a hard cap on concurrent connections (F-07)."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, address, handler, max_connections: int = 48) -> None:
        self.max_connections = max_connections
        self._active = 0
        self._active_lock = threading.Lock()
        self.refused = 0
        super().__init__(address, handler)

    @property
    def active(self) -> int:
        return self._active

    def process_request(self, request, client_address) -> None:
        with self._active_lock:
            if self._active >= self.max_connections:
                self.refused += 1
                full = True
            else:
                self._active += 1
                full = False
        if full:
            self.shutdown_request(request)  # no thread for it
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._active_lock:
                self._active -= 1


def make_server(bbs: StormBBS, host: str | None = None, port: int | None = None) -> PortalServer:
    return PortalServer(
        (host if host is not None else bbs.cfg.listen, port if port is not None else bbs.cfg.web_port),
        make_handler(bbs),
        max_connections=bbs.cfg.max_connections,
    )
