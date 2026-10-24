"""TOML configuration for both services, with validation and defaults."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

NODE_ID_RE = re.compile(r"![0-9a-f]{8}")


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
    reply_timeout_s: float = 25.0
    guest_cooldown_s: float = 30.0
    global_per_minute: float = 6.0
    global_burst: int = 3
    listen: str = "10.66.0.1"
    web_port: int = 80
    portal_url: str = "http://10.66.0.1/"
    title: str = "Summon the Storm"
    db_path: str | None = None

    @classmethod
    def load(cls, data: dict) -> EdgeConfig:
        radio = data.get("radio", {})
        limits = data.get("limits", {})
        web = data.get("web", {})
        store = data.get("store", {})
        portal = str(web.get("portal_url", "http://10.66.0.1/"))
        if not portal.endswith("/"):
            portal += "/"
        return cls(
            radio=RadioConfig.load(radio),
            porch_node_id=_node_id(radio.get("porch_node_id"), "radio.porch_node_id"),
            reply_timeout_s=_num(radio, "reply_timeout_s", 25, 3, 120, "radio"),
            guest_cooldown_s=_num(limits, "guest_cooldown_s", 30, 1, 3600, "limits"),
            global_per_minute=_num(limits, "global_per_minute", 6, 0.1, 120, "limits"),
            global_burst=int(_num(limits, "global_burst", 3, 1, 50, "limits")),
            listen=str(web.get("listen", "10.66.0.1")),
            web_port=int(_num(web, "port", 80, 1, 65535, "web")),
            portal_url=portal,
            title=str(web.get("title", "Summon the Storm"))[:60],
            db_path=store.get("path") or None,
        )


@dataclass
class TranslatorConfig:
    radio: RadioConfig
    edge_node_ids: list[str]
    ha_url: str
    ha_token: str
    ha_verify_tls: bool = True
    ha_ca_file: str | None = None
    ha_timeout_s: float = 5.0
    show_entity: str | None = None
    scripts: dict[str, str] = field(default_factory=dict)
    per_minute: float = 12.0
    burst: int = 3
    state_path: str | None = None
    reset_hour: int = 12

    @classmethod
    def load(cls, data: dict) -> TranslatorConfig:
        radio = data.get("radio", {})
        ha = data.get("homeassistant", {})
        scripts = data.get("scripts", {})
        limits = data.get("limits", {})
        state = data.get("state", {})

        senders = radio.get("edge_node_ids")
        if not isinstance(senders, list) or not senders:
            raise ConfigError("radio.edge_node_ids: list the edge node ID(s), e.g. ['!a1b2c3d4']")
        senders = [_node_id(s, "radio.edge_node_ids") for s in senders]

        url = str(ha.get("url", "")).rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise ConfigError("homeassistant.url: expected http(s)://host:8123")
        token = ha.get("token")
        token_file = ha.get("token_file")
        if token_file:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        token = token or os.environ.get("STORM_HA_TOKEN")
        if not token:
            raise ConfigError("homeassistant: set token_file (preferred), token, or STORM_HA_TOKEN")

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

        show = ha.get("show_entity") or None
        if show is not None and not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", show):
            raise ConfigError(f"homeassistant.show_entity: bad entity id {show!r}")

        return cls(
            radio=RadioConfig.load(radio),
            edge_node_ids=senders,
            ha_url=url,
            ha_token=token,
            ha_verify_tls=bool(ha.get("verify_tls", True)),
            ha_ca_file=ha.get("ca_file") or None,
            ha_timeout_s=_num(ha, "timeout_s", 5, 1, 30, "homeassistant"),
            show_entity=show,
            scripts=resolved,
            per_minute=_num(limits, "per_minute", 12, 0.1, 120, "limits"),
            burst=int(_num(limits, "burst", 3, 1, 20, "limits")),
            state_path=state.get("path") or None,
            reset_hour=int(_num(state, "reset_hour", 12, 0, 23, "state")),
        )


def read_toml(path: str | os.PathLike) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)
