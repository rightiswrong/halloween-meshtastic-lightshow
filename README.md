# 🎃⚡ Halloween Meshtastic Light Show

Guests at the edge of the property summon lightning over the house. They join an open
**Wi-Fi hotspot** and tap a button, or **DM a Storm Bot** from any Meshtastic app. The
overhead lights flash, and the thunder rolls in a moment later.

Nothing a guest touches can reach the home network. The guest-facing side runs on a
battery-powered Raspberry Pi CM4 at the edge, with **no route and no credentials** into
the house. The only thing that crosses into the home network is one short, allowlisted
line of text on a **private, encrypted LoRa channel**. On the house side, a small
translator turns that line into one Home Assistant script.

```
 UNTRUSTED: edge station (battery)                   TRUSTED: home LAN
┌───────────────────────────────────┐   LoRa 915   ┌───────────────────────────────────────┐
│ Guest phone ─Wi-Fi─► CM4 hotspot  │   private    │ Porch T-Beam (Wi-Fi client, TCP 4403) │
│   captive portal + button page    │   PSK chan   │   │                                   │
│   storm-bbs: menu, cooldowns      │  ─────────►  │ storm-translator (Raspberry Pi)       │
│   Edge T-Beam (USB) ◄── mesh DMs  │  SB1 lines   │   allowlist → one script.storm_*      │
│ no LAN route · no HA token        │  ◄─────────  │ Home Assistant → Gledopto + speaker   │
└───────────────────────────────────┘   OK/CLOSED  └───────────────────────────────────────┘
```

Companion firmware (unchanged by this repo):
[esp32-overhead-spooky-lighting](https://github.com/rightiswrong/esp32-overhead-spooky-lighting)
on the Gledopto, plus the **Spooky Sound** integration's `spooky_sound.send_cue` from
[esp32c6-spooky-sound](https://github.com/rightiswrong/esp32c6-spooky-sound).

## Why not an off-the-shelf Meshtastic BBS?

The established Pi-friendly options are good at what they do:

| Project | What it is | Fit here |
|---|---|---|
| [TC²-BBS-mesh](https://github.com/TheCommsChannel/TC2-BBS-mesh) | The best-known Meshtastic BBS: Python, runs on a Pi, with message boards, mail, and a channel directory | Great for a community BBS, but it has no web front end and no way to call out to anything else |
| [meshing-around](https://github.com/SpudGunMan/meshing-around) | A feature-rich Meshtastic bot framework with BBS, games, weather and more | Large and powerful, but much more code on the untrusted box than this job needs |

The deciding constraint is that a Meshtastic node serves **one API client at a time**.
The edge T-Beam therefore has to be shared by the web page and the mesh bot in one
process, with one cooldown policy for both. That process is `storm-bbs`: a few hundred
lines of Python, standard library plus `meshtastic`. If you'd also like a classic BBS
(bulletins and mail), run TC²-BBS-mesh on a **second** T-Beam. It won't interfere.

## What's in the box

| Path | What |
|---|---|
| `src/stormshow/bbs/` | **storm-bbs** for the edge CM4: captive portal + button page, Meshtastic Storm Bot, cooldowns, SQLite log |
| `src/stormshow/translator/` | **storm-translator** for the translator Pi: strict allowlist, Home Assistant REST client, bolt counter |
| `src/stormshow/protocol.py` | The `SB1` wire protocol: the whole attack surface of the trusted side, in about 100 lines |
| `homeassistant/packages/storm.yaml` | HA package: `script.storm_*`, show-hours helper, light snapshot and restore |
| `deploy/edge/` | hostapd, dnsmasq (captive DNS), nftables isolation, systemd units, `install.sh` |
| `deploy/translator/` | Hardened systemd unit and `install.sh` for Raspberry Pi OS (`--usb` for a USB-attached porch radio) |
| `config/*.example.toml` | Annotated configs for both services |
| `docs/` | [Radio setup](docs/RADIO_SETUP.md) · [Home Assistant](docs/HOME_ASSISTANT.md) · [Test plan](docs/TEST_PLAN.md) · [Security model](docs/SECURITY.md) |

## Quick start

1. **Radios.** Flash stock Meshtastic on two 915 MHz T-Beams, then set up the shared
   private channel, the porch Wi-Fi and the edge GPS-off setting: [docs/RADIO_SETUP.md](docs/RADIO_SETUP.md).
2. **Home Assistant.** Install the package, create a dedicated user and token, and add
   your thunder clips: [docs/HOME_ASSISTANT.md](docs/HOME_ASSISTANT.md).
3. **Translator Pi** (latest Raspberry Pi OS Lite, on the Home Assistant subnet):
   ```bash
   git clone https://github.com/rightiswrong/halloween-meshtastic-lightshow && cd halloween-meshtastic-lightshow
   sudo ./deploy/translator/install.sh        # add --usb if the porch T-Beam plugs into this Pi
   sudoedit /etc/storm/translator.toml /etc/storm/ha.token
   sudo -u storm-translator /opt/storm-translator/bin/storm-translator --check
   sudo -u storm-translator /opt/storm-translator/bin/storm-translator --test STRIKE   # lights + thunder?
   sudo systemctl start storm-translator
   ```
4. **Edge CM4** (Raspberry Pi OS Lite, on Ethernet for setup):
   ```bash
   git clone https://github.com/rightiswrong/halloween-meshtastic-lightshow && cd halloween-meshtastic-lightshow
   sudo ./deploy/edge/install.sh
   sudoedit /etc/storm/edge.toml
   sudo reboot
   /opt/storm-bbs/bin/storm-bbs --ping      # round trip to the translator, no action taken
   ```
5. Work through the **[test plan](docs/TEST_PLAN.md)**, T0 to T8.

**Try it without any radios:** `PYTHONPATH=src python3 -m stormshow.bbs --demo`, then open
<http://127.0.0.1:8080/>. Demo mode runs the real BBS and translator over an in-memory
mesh with a fake Home Assistant.

## Commands

| Guest (button or mesh DM) | Sent on the private channel | Home Assistant |
|---|---|---|
| `?` / `HELP` | — (answered on the edge) | — |
| Strike / `STRIKE` / `1` | `SB1 <rid> STRIKE 80` | `script.storm_strike` (strength 0.8) |
| Boom / `BOOM` / `2` | `SB1 <rid> STRIKE 100` | `script.storm_strike` (strength 1.0) |
| Storm / `STORM` / `3` | `SB1 <rid> STORM` | `script.storm_storm` (about 30 s) |
| Fire / `FIRE` / `4` | `SB1 <rid> FIRE` | `script.storm_fire` |
| Heart / `HEART` / `5` | `SB1 <rid> HEART` | `script.storm_heart` |
| `WHO` / `#` / `6` | — (answered on the edge) | — |
| — (`storm-bbs --ping`) | `SB1 <rid> PING` | nothing; replies `PONG` |

The translator replies `OK <bolts tonight>`, `CLOSED` (show hours off), `BUSY` (its own
rate limit) or `ERR` (HA failed). The edge turns these into guest messages and gives back
the cooldown when nothing happened.

## Development

```bash
python3 -m unittest discover -s tests -t .     # 45 tests, standard library only (no radios)
ruff check src tests
```

The tests cover:

- **Protocol:** the grammar, including about 25 malformed or hostile lines.
- **Rate limits:** per-guest cooldowns and the global limit.
- **Translator rejections:** wrong channel, unknown sender, bad syntax, duplicates, its own rate limit.
- **End to end:** the BBS and translator over a simulated mesh, including timeouts, forged replies and DMs.
- **Captive portal:** the probe redirects, the page, and bad requests.
- **HA client:** the real REST client against a local stand-in server.

## Status

- ✅ All unit and end-to-end tests pass on a PC. Demo mode serves the page, and the full
  guest → BBS → translator → HA path runs in simulation.
- ⚠️ **Not yet run on the real radios, CM4 or Home Assistant.** The `meshtastic` library
  calls follow its 2.x API (`SerialInterface`, `TCPInterface`, `sendText`, pubsub
  `meshtastic.receive.text`). Hostapd and nftables configs are syntax-checked, not field-tested.
- ⚠️ The HA package's light snapshot and restore logic is untested in a live HA instance.
  Run T0 first.
