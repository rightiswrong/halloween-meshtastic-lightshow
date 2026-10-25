"""TOML configuration for both services, with validation and defaults."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

from .protocol import LinkKey

NODE_ID_RE = re.compile(r"![0-9a-f]{8}")
WEBHOOK_ID_RE = re.compile(r"[A-Za-z0-9_-]{24,128}")
HOURS_RE = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])-([01][0-9]|2[0-3]):([0-5][0-9])")


class ConfigError(ValueError):
    pass


def _node_id(value: object, where: str) -> str:
    if not isinstance(value, str) or not NODE_ID_RE.fullmatch(value.lower()):
        raise ConfigError(f"{where}: expected a node ID like '!a1b2c3d4', got {value!r}")
    return value.lower()


def _num(table: dict, key: str, default: float, lo: float, hi: float, where: str) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
        raise ConfigError(f"{where}.{key}: expected a number in {lo}..{hi}, got {value!r}")
    return value


def _secret_text(table: dict, key: str, where: str) -> str | None:
    """A secret from '<key>_file' (preferred) or inline '<key>'."""
    path = table.get(f"{key}_file")
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError as err:
            raise ConfigError(f"{where}.{key}_file: cannot read {path}: {err.strerror}") from err
    value = table.get(key)
    return str(value).strip() if value else None


def _link(data: dict) -> tuple[LinkKey, int]:
    link = data.get("link", {})
    text = _secret_text(link, "secret", "link")
    if not text:
        raise ConfigError("[link]: set secret_file (generate with: openssl rand -base64 32)")
    try:
        key = LinkKey.from_base64(text)
    except ValueError as err:
        raise ConfigError(f"[link] secret: {err}") from err
    hop_limit = int(_num(link, "hop_limit", 1, 0, 7, "link"))
    return key, hop_limit


def parse_hours(text: str) -> tuple[time, time]:
    m = HOURS_RE.fullmatch(text.strip())
    if not m:
        raise ConfigError(f"[show] hours: expected 'HH:MM-HH:MM', got {text!r}")
    h1, m1, h2, m2 = (int(g) for g in m.groups())
    return time(h1, m1), time(h2, m2)


@dataclass
class RadioConfig:
    """How to reach the local Meshtastic node: USB serial (``port``) or TCP (``host``)."""

    port: str | None = None
    host: str | None = None
    private_channel: int = 1

    @classmethod
    def load(cls, t: dict) -> RadioConfig:
        port, host = t.get("port"), t.get("host")
        if bool(port) == bool(host):
            raise ConfigError("[radio]: set exactly one of 'port' (serial) or 'host' (TCP)")
        ch = int(_num(t, "private_channel", 1, 1, 7, "radio"))
        return cls(port=port or None, host=host or None, private_channel=ch)


@dataclass
class EdgeConfig:
    radio: RadioConfig
    porch_node_id: str
    link_key: LinkKey
    hop_limit: int = 1
    reply_timeout_s: float = 25.0
    # cooldowns and budgets (SECURITY-AUDIT.md F-05, F-06, F-10)
    guest_cooldown_s: float = 30.0
    web_per_minute: float = 6.0
    web_burst: int = 3
    mesh_per_minute: float = 2.0
    mesh_burst: int = 2
    mesh_replies_per_minute: float = 20.0
    mesh_reply_cooldown_s: float = 10.0
    max_pending_dms: int = 8
    # web (F-07)
    listen: str = "10.66.0.1"
    web_port: int = 80
    portal_url: str = "http://10.66.0.1/"
    title: str = "Summon the Storm"
    max_connections: int = 48
    dhcp_leases: str | None = "/var/lib/misc/dnsmasq.leases"
    # state and privacy (F-17)
    db_path: str | None = None
    seq_path: str | None = None
    show_mesh_names: bool = False
    log_retention_days: int = 7

    @classmethod
    def load(cls, data: dict) -> EdgeConfig:
        radio = data.get("radio", {})
        limits = data.get("limits", {})
        web = data.get("web", {})
        store = data.get("store", {})
        privacy = data.get("privacy", {})
        key, hop_limit = _link(data)
        portal = str(web.get("portal_url", "http://10.66.0.1/"))
        if not portal.endswith("/"):
            portal += "/"
        # 'global_*' are the pre-audit names for the web budget; still accepted.
        web_rate = limits.get("web_per_minute", limits.get("global_per_minute", 6))
        web_burst = limits.get("web_burst", limits.get("global_burst", 3))
        return cls(
            radio=RadioConfig.load(radio),
            porch_node_id=_node_id(radio.get("porch_node_id"), "radio.porch_node_id"),
            link_key=key,
            hop_limit=hop_limit,
            reply_timeout_s=_num(radio, "reply_timeout_s", 25, 3, 120, "radio"),
            guest_cooldown_s=_num(limits, "guest_cooldown_s", 30, 1, 3600, "limits"),
            web_per_minute=_num({"v": web_rate}, "v", 6, 0.1, 120, "limits.web_per_minute"),
            web_burst=int(_num({"v": web_burst}, "v", 3, 1, 50, "limits.web_burst")),
            mesh_per_minute=_num(limits, "mesh_per_minute", 2, 0.1, 60, "limits"),
            mesh_burst=int(_num(limits, "mesh_burst", 2, 1, 20, "limits")),
            mesh_replies_per_minute=_num(limits, "mesh_replies_per_minute", 20, 1, 120, "limits"),
            mesh_reply_cooldown_s=_num(limits, "mesh_reply_cooldown_s", 10, 1, 600, "limits"),
            max_pending_dms=int(_num(limits, "max_pending_dms", 8, 1, 64, "limits")),
            listen=str(web.get("listen", "10.66.0.1")),
            web_port=int(_num(web, "port", 80, 1, 65535, "web")),
            portal_url=portal,
            title=str(web.get("title", "Summon the Storm"))[:60],
            max_connections=int(_num(web, "max_connections", 48, 4, 512, "web")),
            dhcp_leases=web.get("dhcp_leases", "/var/lib/misc/dnsmasq.leases") or None,
            db_path=store.get("path") or None,
            seq_path=store.get("seq_path") or None,
            show_mesh_names=bool(privacy.get("show_mesh_names", False)),
            log_retention_days=int(_num(privacy, "log_retention_days", 7, 0, 3650, "privacy")),
        )


@dataclass
class TranslatorConfig:
    radio: RadioConfig
    edge_node_ids: list[str]
    link_key: LinkKey
    ha_url: str
    hop_limit: int = 1
    # "webhook" (recommended, no token on this box) or "token" (REST API)
    ha_mode: str = "webhook"
    ha_webhook_id: str | None = None
    ha_token: str | None = None
    ha_verify_tls: bool = True
    ha_ca_file: str | None = None
    ha_timeout_s: float = 5.0
    show_entity: str | None = None  # token mode only
    show_hours: tuple[time, time] | None = None  # local schedule, both modes
    scripts: dict[str, str] = field(default_factory=dict)
    per_minute: float = 12.0
    burst: int = 3
    ping_per_minute: float = 6.0
    max_queue: int = 16
    state_path: str | None = None
    link_state_path: str | None = None
    reset_hour: int = 12

    @classmethod
    def load(cls, data: dict) -> TranslatorConfig:
        radio = data.get("radio", {})
        ha = data.get("homeassistant", {})
        scripts = data.get("scripts", {})
        limits = data.get("limits", {})
        state = data.get("state", {})
        show = data.get("show", {})

        senders = radio.get("edge_node_ids")
        if not isinstance(senders, list) or not senders:
            raise ConfigError("radio.edge_node_ids: list the edge node ID(s), e.g. ['!a1b2c3d4']")
        senders = [_node_id(s, "radio.edge_node_ids") for s in senders]
        key, hop_limit = _link(data)

        url = str(ha.get("url", "")).rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise ConfigError("homeassistant.url: expected http(s)://host:8123")

        mode = str(ha.get("mode", "webhook"))
        webhook_id = token = None
        if mode == "webhook":
            webhook_id = _secret_text(ha, "webhook_id", "homeassistant")
            if not webhook_id or not WEBHOOK_ID_RE.fullmatch(webhook_id):
                raise ConfigError(
                    "homeassistant: set webhook_id_file to a random id of 24-128 characters "
                    "[A-Za-z0-9_-] (generate with: openssl rand -hex 24)"
                )
        elif mode == "token":
            token = _secret_text(ha, "token", "homeassistant") or os.environ.get("STORM_HA_TOKEN")
            if not token:
                raise ConfigError("homeassistant: token mode needs token_file (or STORM_HA_TOKEN)")
        else:
            raise ConfigError(f"homeassistant.mode: 'webhook' or 'token', got {mode!r}")

        defaults = {
            "STRIKE": "script.storm_strike",
            "STORM": "script.storm_storm",
            "FIRE": "script.storm_fire",
            "HEART": "script.storm_heart",
        }
        resolved = {}
        for verb, default in defaults.items():
            entity = str(scripts.get(verb.lower(), default))
            if not re.fullmatch(r"script\.[a-z0-9_]+", entity):
                raise ConfigError(f"scripts.{verb.lower()}: expected script.<name>, got {entity!r}")
            resolved[verb] = entity

        show_entity = ha.get("show_entity") or None
        if show_entity is not None and not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", show_entity):
            raise ConfigError(f"homeassistant.show_entity: bad entity id {show_entity!r}")
        hours = parse_hours(show["hours"]) if show.get("hours") else None

        return cls(
            radio=RadioConfig.load(radio),
            edge_node_ids=senders,
            link_key=key,
            hop_limit=hop_limit,
            ha_url=url,
            ha_mode=mode,
            ha_webhook_id=webhook_id,
            ha_token=token,
            ha_verify_tls=bool(ha.get("verify_tls", True)),
            ha_ca_file=ha.get("ca_file") or None,
            ha_timeout_s=_num(ha, "timeout_s", 5, 1, 30, "homeassistant"),
            show_entity=show_entity if mode == "token" else None,
            show_hours=hours,
            scripts=resolved,
            per_minute=_num(limits, "per_minute", 12, 0.1, 120, "limits"),
            burst=int(_num(limits, "burst", 3, 1, 20, "limits")),
            ping_per_minute=_num(limits, "ping_per_minute", 6, 0.1, 60, "limits"),
            max_queue=int(_num(limits, "max_queue", 16, 1, 256, "limits")),
            state_path=state.get("path") or None,
            link_state_path=state.get("link_path") or None,
            reset_hour=int(_num(state, "reset_hour", 12, 0, 23, "state")),
        )


def read_toml(path: str | os.PathLike) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)
