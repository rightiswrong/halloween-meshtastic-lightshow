"""Radio self-checks (F-04, F-11) and packet helpers."""

import unittest
from types import SimpleNamespace

from stormshow.mesh import packet_channel, packet_from, packet_to, parse_version, radio_warnings


def iface(version="2.7.15.abc", bt=False):
    return SimpleNamespace(
        metadata=SimpleNamespace(firmware_version=version),
        localNode=SimpleNamespace(localConfig=SimpleNamespace(bluetooth=SimpleNamespace(enabled=bt))),
    )


class Checks(unittest.TestCase):
    def test_version_parse(self):
        self.assertEqual(parse_version("2.7.15.567b8ea"), (2, 7, 15))
        self.assertEqual(parse_version("2.6.11"), (2, 6, 11))
        self.assertEqual(parse_version(""), ())

    def test_good_radio_has_no_warnings(self):
        self.assertEqual(radio_warnings(iface()), [])

    def test_old_firmware_and_bluetooth_warn(self):
        warnings = radio_warnings(iface("2.6.11.60ec05e", bt=True))
        self.assertTrue(any("CVE-2025-53627" in w for w in warnings))
        self.assertTrue(any("Bluetooth is ON" in w for w in warnings))

    def test_unknown_radio(self):
        self.assertEqual(len(radio_warnings(SimpleNamespace())), 2)


class Packets(unittest.TestCase):
    def test_fields(self):
        p = {"from": 0xA1B2C3D4, "to": 0xFFFFFFFF, "channel": 1, "decoded": {"text": "x"}}
        self.assertEqual(packet_from(p), "!a1b2c3d4")
        self.assertEqual(packet_to(p), "^all")
        self.assertEqual(packet_channel(p), 1)
        self.assertEqual(packet_channel({}), 0)


if __name__ == "__main__":
    unittest.main()
