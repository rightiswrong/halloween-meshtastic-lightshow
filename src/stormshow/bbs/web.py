"""The guest web page and captive portal, on the standard library's HTTP server.

Phones joining the hotspot probe a well-known URL (Apple: /hotspot-detect.html,
Android: /generate_204, Windows: /connecttest.txt, ...). dnsmasq answers every DNS
name with this box, so those probes land here; answering anything but the page
itself with a redirect makes the phone open the portal window on its own.
"""

from __future__ import annotations

import json
import logging
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import urlsplit

from .service import StormBBS

log = logging.getLogger(__name__)

MAX_BODY = 256
GUEST_NAME = "a guest at the gate"


def _page(title: str) -> bytes:
    html = resources.files(__package__).joinpath("static/index.html").read_text(encoding="utf-8")
    return html.replace("{{TITLE}}", escape(title)).encode("utf-8")


def make_handler(bbs: StormBBS) -> type[BaseHTTPRequestHandler]:
    cfg = bbs.cfg
    portal = urlsplit(cfg.portal_url)
    portal_host = portal.netloc.lower()
    page = _page(cfg.title)

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
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
            )
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
            return (self.headers.get("Host") or "").lower() == portal_host

        # -------------------------------------------------------------- verbs
        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if not self._on_portal():
                return self._redirect()
            if path == "/":
                return self._send(200, page, "text/html; charset=utf-8")
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
            result = bbs.summon(f"web:{self.client_address[0]}", GUEST_NAME, word)
            code = 429 if result.status == "cooldown" else 200
            return self._json(code, result.as_dict())

    return Handler


class PortalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32


def make_server(bbs: StormBBS, host: str | None = None, port: int | None = None) -> PortalServer:
    return PortalServer(
        (host if host is not None else bbs.cfg.listen, port if port is not None else bbs.cfg.web_port),
        make_handler(bbs),
    )
