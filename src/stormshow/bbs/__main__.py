"""``storm-bbs``: the edge station service (web portal + Meshtastic BBS)."""

from __future__ import annotations

import argparse
import logging
import secrets
import signal
import sys
import threading

from ..config import ConfigError, EdgeConfig, RadioConfig, read_toml
from .service import StormBBS
from .store import Store
from .web import make_server

log = logging.getLogger("storm-bbs")


def _demo_config() -> EdgeConfig:
    from ..protocol import LinkKey

    return EdgeConfig(
        radio=RadioConfig(port="loopback"),
        porch_node_id="!0000beef",
        link_key=LinkKey(secrets.token_bytes(32)),
        reply_timeout_s=5,
        guest_cooldown_s=10,
        listen="127.0.0.1",
        web_port=8080,
        portal_url="http://127.0.0.1:8080/",
        title="Summon the Storm (demo)",
    )


def _start_demo(cfg: EdgeConfig):
    """Wire the BBS to an in-memory mesh with a translator and a fake Home Assistant."""
    from ..config import TranslatorConfig
    from ..loopback import FakeHomeAssistant, LoopbackMesh
    from ..translator.service import BoltCounter, Translator

    mesh = LoopbackMesh()
    edge = mesh.node("!0000ed9e", "Storm Station")
    porch = mesh.node(cfg.porch_node_id, "Porch")
    tcfg = TranslatorConfig(
        radio=RadioConfig(host="loopback", private_channel=cfg.radio.private_channel),
        edge_node_ids=[edge.node_id],
        link_key=cfg.link_key,
        ha_url="http://fake",
        ha_webhook_id="demo-webhook-id-000000000000",
        scripts={v: f"script.storm_{v.lower()}" for v in ("STRIKE", "STORM", "FIRE", "HEART")},
    )
    translator = Translator(tcfg, FakeHomeAssistant(), porch, BoltCounter(None, 12))
    porch.handler = translator.on_packet
    return edge, translator


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="storm-bbs", description=__doc__)
    ap.add_argument("-c", "--config", default="/etc/storm/edge.toml")
    ap.add_argument("--demo", action="store_true", help="no radio: in-memory mesh and a fake Home Assistant")
    ap.add_argument("--ping", action="store_true", help="send one PING to the translator, print the result, exit")
    ap.add_argument("--listen", help="override [web] listen address")
    ap.add_argument("--port", type=int, help="override [web] port")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("meshtastic", "pubsub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    link = None
    try:
        if args.demo:
            cfg = _demo_config()
            transport, _translator = _start_demo(cfg)
        else:
            cfg = EdgeConfig.load(read_toml(args.config))
            from ..mesh import MeshLink

            holder: dict = {}
            link = MeshLink(cfg.radio, lambda p: holder["bbs"].on_packet(p))
            transport = link
    except (ConfigError, OSError) as err:
        print(f"storm-bbs: config error: {err}", file=sys.stderr)
        return 2

    store = Store(cfg.db_path, cfg.log_retention_days) if cfg.db_path and not args.demo else None
    bbs = StormBBS(cfg, transport, store)
    if args.demo:
        transport.handler = bbs.on_packet
    if link is not None:
        holder["bbs"] = bbs
        link.start()
        if not link.wait_ready(timeout=120):
            log.warning("radio not connected yet; the page will report errors until it is")

    if args.ping:
        reply = bbs.ping()
        print(f"translator: {reply.status if reply else 'no reply'}")
        return 0 if reply else 1

    server = make_server(bbs, args.listen, args.port)
    stop = threading.Event()

    def _shutdown(*_):
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    log.info("guest portal on http://%s:%s/", *server.server_address[:2])
    try:
        server.serve_forever()
    finally:
        server.server_close()
        bbs.close()
        if link is not None:
            link.stop()
        if store is not None:
            store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
