import tempfile
import unittest
from pathlib import Path

from stormshow.config import ConfigError, EdgeConfig, TranslatorConfig, read_toml

ROOT = Path(__file__).resolve().parent.parent


class ExampleConfigs(unittest.TestCase):
    def test_edge_example_loads(self):
        cfg = EdgeConfig.load(read_toml(ROOT / "config/edge.example.toml"))
        self.assertEqual(cfg.radio.port, "/dev/ttyACM0")
        self.assertEqual(cfg.radio.private_channel, 1)
        self.assertTrue(cfg.portal_url.endswith("/"))

    def test_translator_example_loads(self):
        with tempfile.NamedTemporaryFile("w", suffix=".token", delete=False) as fh:
            fh.write("abc.def.ghi\n")
        text = (ROOT / "config/translator.example.toml").read_text()
        text = text.replace("/etc/storm/ha.token", fh.name)
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as cf:
            cf.write(text)
        cfg = TranslatorConfig.load(read_toml(cf.name))
        self.assertEqual(cfg.ha_token, "abc.def.ghi")
        self.assertEqual(cfg.scripts["STRIKE"], "script.storm_strike")
        self.assertEqual(cfg.radio.host, "porch-tbeam.lan")


class Validation(unittest.TestCase):
    base_edge = {"radio": {"port": "/dev/ttyACM0", "porch_node_id": "!a1b2c3d4"}}

    def test_needs_exactly_one_radio_link(self):
        with self.assertRaises(ConfigError):
            EdgeConfig.load({"radio": {"porch_node_id": "!a1b2c3d4"}})
        with self.assertRaises(ConfigError):
            EdgeConfig.load({"radio": {"port": "x", "host": "y", "porch_node_id": "!a1b2c3d4"}})

    def test_node_ids(self):
        with self.assertRaises(ConfigError):
            EdgeConfig.load({"radio": {"port": "x", "porch_node_id": "a1b2c3d4"}})
        cfg = EdgeConfig.load({"radio": {"port": "x", "porch_node_id": "!A1B2C3D4"}})
        self.assertEqual(cfg.porch_node_id, "!a1b2c3d4")

    def test_private_channel_cannot_be_primary(self):
        with self.assertRaises(ConfigError):
            EdgeConfig.load({"radio": {"port": "x", "porch_node_id": "!a1b2c3d4", "private_channel": 0}})

    def test_translator_requires_token_and_scripts(self):
        base = {
            "radio": {"host": "h", "edge_node_ids": ["!a1b2c3d4"]},
            "homeassistant": {"url": "http://ha:8123", "token": "t"},
        }
        TranslatorConfig.load(base)
        with self.assertRaises(ConfigError):
            TranslatorConfig.load({**base, "homeassistant": {"url": "http://ha:8123"}})
        with self.assertRaises(ConfigError):
            TranslatorConfig.load({**base, "scripts": {"strike": "light.turn_on"}})
        with self.assertRaises(ConfigError):
            TranslatorConfig.load({**base, "radio": {"host": "h", "edge_node_ids": []}})


if __name__ == "__main__":
    unittest.main()
