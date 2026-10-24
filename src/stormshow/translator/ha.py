"""Minimal Home Assistant REST client (standard library only)."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from urllib.parse import quote


class HAError(RuntimeError):
    pass


class HomeAssistant:
    def __init__(
        self,
        url: str,
        token: str,
        timeout_s: float = 5.0,
        verify_tls: bool = True,
        ca_file: str | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self._timeout = timeout_s
        self._ctx: ssl.SSLContext | None = None
        if self.url.startswith("https://"):
            self._ctx = ssl.create_default_context(cafile=ca_file)
            if not verify_tls:
                self._ctx.check_hostname = False
                self._ctx.verify_mode = ssl.CERT_NONE

    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout, context=self._ctx) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as err:
            return err.code, b""
        except (urllib.error.URLError, OSError, TimeoutError) as err:
            raise HAError(f"Home Assistant unreachable: {err}") from err

    def check(self) -> str:
        """Verify the URL and token. Returns HA's message ('API running.')."""
        code, body = self._request("GET", "/api/")
        if code != 200:
            raise HAError(f"GET /api/ returned HTTP {code} (bad token?)")
        return json.loads(body or b"{}").get("message", "ok")

    def state(self, entity_id: str) -> str | None:
        """An entity's state string, or None if the entity doesn't exist."""
        code, body = self._request("GET", "/api/states/" + quote(entity_id, safe="._"))
        if code == 404:
            return None
        if code != 200:
            raise HAError(f"GET state of {entity_id} returned HTTP {code}")
        return json.loads(body).get("state")

    def run_script(self, entity_id: str, variables: dict | None = None) -> None:
        """Start a script without waiting for it to finish (script.turn_on)."""
        body: dict = {"entity_id": entity_id}
        if variables:
            body["variables"] = variables
        code, _ = self._request("POST", "/api/services/script/turn_on", body)
        if code != 200:
            raise HAError(f"script.turn_on {entity_id} returned HTTP {code}")
