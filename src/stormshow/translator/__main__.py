"""``storm-translator``: the trusted-side service (porch radio -> Home Assistant)."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from .. import protocol as P
from ..config import ConfigError, TranslatorConfig, read_toml
from .ha import HAError, make_client
from .service import BoltCounter, Translator

log = logging.getLogger("storm-translator")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="storm-translator", description=__doc__)
    ap.add_argument("-c", "--config", default="/etc/storm/translator.toml")
    ap.add_argument("--check", action="store_true", help="check the HA URL, token and show entity, then exit")
    ap.add_argument(
        "--test",
        metavar="VERB",
        choices=sorted(P.VERBS - {"PING"}),
        help="run one action against HA without the radio (test T0), then exit",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("meshtastic", "pubsub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        cfg = TranslatorConfig.load(read_toml(args.config))
    except (ConfigError, OSError) as err:
        print(f"storm-translator: config error: {err}", file=sys.stderr)
        return 2

    ha = make_client(cfg)
    if cfg.ha_mode == "token":
        log.warning("token mode: this Pi holds a token that can call ANY Home Assistant service; prefer webhook mode")

    if args.check:
        try:
            print(f"mode: {cfg.ha_mode}")
            print(f"Home Assistant: {ha.check()}")
            if cfg.show_hours:
                print(f"show hours: {cfg.show_hours[0]:%H:%M}-{cfg.show_hours[1]:%H:%M} (local time)")
            if cfg.ha_mode == "token":
                if cfg.show_entity:
                    print(f"{cfg.show_entity} = {ha.state(cfg.show_entity)}")
                for verb, entity in cfg.scripts.items():
                    print(f"{verb:7} -> {entity}: {ha.state(entity) or 'MISSING'}")
        except HAError as err:
            print(f"FAILED: {err}", file=sys.stderr)
            return 1
        return 0

    if args.test:
        level = 80 if args.test == "STRIKE" else None
        counter = BoltCounter(None, cfg.reset_hour)
        t = Translator(cfg, ha, transport=_NoRadio(), counter=counter)
        reply = t.execute(P.Request(1, args.test, level))
        print(f"{args.test}: {reply.status}")
        return 0 if reply.status in (P.STATUS_OK, P.STATUS_CLOSED) else 1

    from ..mesh import MeshLink

    holder: dict = {}
    link = MeshLink(cfg.radio, lambda p: holder["t"].on_packet(p))
    translator = Translator(cfg, ha, link)
    holder["t"] = translator

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    try:
        log.info("Home Assistant: %s", ha.check())
    except HAError as err:
        log.error("%s (continuing; requests will answer ERR until HA is reachable)", err)
    log.info("allowlisted edge nodes: %s; private channel %d", ", ".join(cfg.edge_node_ids), cfg.radio.private_channel)
    link.start()
    stop.wait()
    log.info("stopping; stats %s", translator.stats)
    translator.close()
    link.stop()
    return 0


class _NoRadio:
    def my_node_id(self):
        return None

    def node_name(self, node_id):
        return None

    def send_text(self, text, destination, channel, hop_limit=None):
        pass


if __name__ == "__main__":
    sys.exit(main())
