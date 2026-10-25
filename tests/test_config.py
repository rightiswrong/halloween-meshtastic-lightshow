import base64
import os
import tempfile
import unittest
from datetime import time
from pathlib import Path

from stormshow.config import ConfigError, EdgeConfig, TranslatorConfig, read_toml

ROOT = Path(__file__).resolve().parent.parent
SECRET = base64.b64encode(bytes(range(32))).decode()


def _write(text: str, suffix: str = "") -> str:
    fh = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False)
    fh.write(text)
    fh.close()
    return fh.name


class ExampleConfigs(unittest.TestCase):
    def setUp(self):
        self.secret = _write(SECRET + "\n")
        self.hook = _write("0123456789abcdef0123456789abcdef0123456789abcdef\n")

    def load(self, name: str) -> dict:
        text = (ROOT / "config" / name).read_text()
        text = text.replace("/etc/storm/link.secret", self.secret).replace("/etc/storm/webhook.id", self.hook)
        return read_toml(_write(text, ".toml"))

    def test_edge_example_loads(self):
        cfg = EdgeConfig.load(self.load("edge.example.toml"))
        self.assertEqual(cfg.radio.port, "/dev/ttyACM0")
        self.assertEqual(cfg.hop_limit, 1)
        self.assertEqual(cfg.mesh_per_minute, 2)
        self.assertFalse(cfg.show_mesh_names)
        self.assertTrue(cfg.seq_path)

    def test_translator_example_loads(self):
        cfg = TranslatorConfig.load(self.load("translator.example.toml"))
        self.assertEqual(cfg.ha_mode, "webhook")
        self.assertIsNone(cfg.ha_token)
        self.assertEqual(cfg.show_hours, (time(18, 0), time(22, 0)))
        self.assertTrue(cfg.link_state_path)


class Validation(unittest.TestCase):
    def setUp(self):
        self.link = {"secret": SECRET}

    def edge(self, **radio):
        return {"radio": {"port": "/dev/ttyACM0", "porch_node_id": "!a1b2c3d4", **radio}, "link": self.link}

    def test_needs_exactly_one_radio_link(self):
        with self.assertRaises(ConfigError):
            EdgeConfig.load({"radio": {"porch_node_id": "!a1b2c3d4"}, "link": self.link})
        with self.assertRaises(ConfigError):
            EdgeConfig.load(self.edge(host="y"))

    def test_link_secret_required_and_checked(self):
        with self.assertRaises(ConfigError):
            EdgeConfig.load({"radio": self.edge()["radio"]})
        with self.assertRaises(ConfigError):
            EdgeConfig.load({**self.edge(), "link": {"secret": base64.b64encode(b"short").decode()}})
        with self.assertRaises(ConfigError):
            EdgeConfig.load({**self.edge(), "link": {"secret_file": "/nonexistent/secret"}})

    def test_node_ids(self):
        with self.assertRaises(ConfigError):
            EdgeConfig.load(self.edge(porch_node_id="a1b2c3d4"))
        self.assertEqual(EdgeConfig.load(self.edge(porch_node_id="!A1B2C3D4")).porch_node_id, "!a1b2c3d4")

    def test_private_channel_cannot_be_primary(self):
        with self.assertRaises(ConfigError):
            EdgeConfig.load(self.edge(private_channel=0))

    def test_old_global_limit_names_still_work(self):
        cfg = EdgeConfig.load({**self.edge(), "limits": {"global_per_minute": 9, "global_burst": 4}})
        self.assertEqual((cfg.web_per_minute, cfg.web_burst), (9, 4))

    def test_translator_modes(self):
        base = {
            "radio": {"host": "h", "edge_node_ids": ["!a1b2c3d4"]},
            "link": self.link,
            "homeassistant": {"url": "http://ha:8123", "webhook_id": "a" * 32},
        }
        self.assertEqual(TranslatorConfig.load(base).ha_mode, "webhook")
        with self.assertRaises(ConfigError):  # webhook id too short to be a secret
            TranslatorConfig.load({**base, "homeassistant": {"url": "http://ha:8123", "webhook_id": "abc"}})
        token = {"url": "http://ha:8123", "mode": "token", "token": "t", "show_entity": "input_boolean.x"}
        cfg = TranslatorConfig.load({**base, "homeassistant": token})
        self.assertEqual((cfg.ha_mode, cfg.show_entity), ("token", "input_boolean.x"))
        with self.assertRaises(ConfigError):
            TranslatorConfig.load({**base, "homeassistant": {"url": "http://ha:8123", "mode": "token"}})
        with self.assertRaises(ConfigError):
            TranslatorConfig.load({**base, "scripts": {"strike": "light.turn_on"}})
        with self.assertRaises(ConfigError):
            TranslatorConfig.load({**base, "show": {"hours": "6pm-10pm"}})

    def tearDown(self):
        os.environ.pop("STORM_HA_TOKEN", None)


if __name__ == "__main__":
    unittest.main()
