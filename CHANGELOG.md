# Changelog

## v1.0.0 — 2026-10-25

First release. Guests summon lightning and thunder from an open hotspot page or a
Meshtastic DM. A battery-powered edge station (CM4 + T-Beam) talks over a private LoRa
channel to a translator Pi, which calls Home Assistant. Nothing a guest touches has a
network path into the home.

### Components
- **storm-bbs** (edge CM4): captive portal and button page, Meshtastic Storm Bot,
  cooldowns, SQLite log.
- **storm-translator** (Raspberry Pi on the HA subnet): verifies signed commands and
  calls one Home Assistant webhook.
- **storm-sign**: makes and checks signed lines for bench tests.
- **Home Assistant package**: four storm scripts, show hours, light snapshot and
  restore, and the `storm_webhook` automation.
- **Deployment**: hostapd, dnsmasq, nftables and systemd files, and installers for
  Raspberry Pi OS.

### Security (all 21 findings of SECURITY-AUDIT.md addressed)
- Signed `SB2` protocol: HMAC-SHA256 with a separate link secret, sequence numbers,
  and a persistent replay window. Replaces `SB1`.
- Webhook mode by default: no Home Assistant token on the translator.
- Separate web and mesh budgets; throttled bot replies; HTTP connection cap; per-guest
  firewall limits; systemd resource limits.
- Default-drop firewall, keys-only SSH option, unprivileged pip install.
- Startup checks for Bluetooth on and firmware older than 2.7.15.
- Hop limit 1 on private traffic; mesh names hidden; 7-day log retention.
- See [docs/HARDENED_DESIGN.md](docs/HARDENED_DESIGN.md) and
  [SECURITY-AUDIT.md](SECURITY-AUDIT.md).

### Known limitations
- Not yet run on the real radios, Pis or a live Home Assistant. Work through
  [docs/TEST_PLAN.md](docs/TEST_PLAN.md) before the show.
- No hash-locked requirements file yet (audit F-13).
