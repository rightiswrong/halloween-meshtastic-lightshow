"""Home Assistant clients (standard library only).

Two ways in:

* ``WebhookHA`` (recommended, SECURITY-AUDIT.md F-02): POSTs ``{"verb", "strength"}`` to
  one ``/api/webhook/<id>`` handled by the ``storm_webhook`` automation in
  ``homeassistant/packages/storm.yaml``. This Pi holds **no token**: a stolen webhook id
  can only run the four Halloween effects, never unlock a door.
* ``TokenHA``: a long-lived access token and ``script.turn_on``. HA tokens can call
  *any* service, so this mode is kept only for compatibility.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from urllib.parse import quote


class HAError(RuntimeError):
    pass


class _Client:
    def __init__(self, url: str, timeout_s: float, verify_tls: bool, ca_file: str | None) -> None:
        self.url = url.rstrip("/")
        self._timeout = timeout_s
        self._headers = {"Content-Type": "application/json"}
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

    def __repr__(self) -> str:  # keep secrets out of logs and tracebacks
        return f"{type(self).__name__}({self.url})"


class WebhookHA(_Client):
    has_state = False

    def __init__(
        self, url: str, webhook_id: str, timeout_s: float = 5.0, verify_tls: bool = True, ca_file: str | None = None
    ) -> None:
        super().__init__(url, timeout_s, verify_tls, ca_file)
        self._path = "/api/webhook/" + quote(webhook_id, safe="")

    def trigger(self, verb: str, strength: float | None) -> None:
        body: dict = {"verb": verb}
        if strength is not None:
            body["strength"] = strength
        code, _ = self._request("POST", self._path, body)
        if code != 200:
            raise HAError(f"webhook returned HTTP {code}")

    def check(self) -> str:
        # The automation ignores unknown verbs, so this only proves HA is reachable.
        self.trigger("PING", None)
        return "webhook reachable (HA always answers 200; check the automation's trace)"

    def state(self, entity_id: str) -> str | None:
        raise HAError("webhook mode cannot read entity states")


class TokenHA(_Client):
    has_state = True

    def __init__(
        self,
        url: str,
        token: str,
        scripts: dict[str, str],
        timeout_s: float = 5.0,
        verify_tls: bool = True,
        ca_file: str | None = None,
    ) -> None:
        super().__init__(url, timeout_s, verify_tls, ca_file)
        self._headers["Authorization"] = f"Bearer {token}"
        self._scripts = scripts

    def check(self) -> str:
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

    def trigger(self, verb: str, strength: float | None) -> None:
        """Start the verb's script without waiting for it to finish (script.turn_on)."""
        body: dict = {"entity_id": self._scripts[verb]}
        if strength is not None:
            body["variables"] = {"strength": strength}
        code, _ = self._request("POST", "/api/services/script/turn_on", body)
        if code != 200:
            raise HAError(f"script.turn_on {self._scripts[verb]} returned HTTP {code}")


def make_client(cfg) -> WebhookHA | TokenHA:
    if cfg.ha_mode == "webhook":
        return WebhookHA(cfg.ha_url, cfg.ha_webhook_id, cfg.ha_timeout_s, cfg.ha_verify_tls, cfg.ha_ca_file)
    return TokenHA(cfg.ha_url, cfg.ha_token, cfg.scripts, cfg.ha_timeout_s, cfg.ha_verify_tls, cfg.ha_ca_file)
