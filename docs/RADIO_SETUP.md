# Radio setup (two LilyGO T-Beams, US 915 MHz)

Both radios run **stock Meshtastic, version 2.7.15 or newer** (older firmware has
CVE-2025-53627; both services log a `RADIO CHECK` warning if a radio is older). Use the
[web flasher](https://flasher.meshtastic.org) and pick your T-Beam's exact board revision. Check that the board is a 915 MHz unit:
T-Beams are also sold as 433 and 868 MHz versions.

The commands below use the `meshtastic` CLI (`pipx install meshtastic`) over USB. Each
`--set` makes the radio reboot, so give it a few seconds between commands.

## 1. Both radios

```bash
meshtastic --set lora.region US
# Keep the default LongFast preset on the primary channel, so ordinary mesh users can DM the bot.

# Bluetooth OFF on both radios, in every mode. Screenless boards default to PIN 123456,
# so anyone on the pavement could pair and read the channel keys (SECURITY-AUDIT.md F-04).
meshtastic --set bluetooth.enabled false
```

Both services check this at startup and log `RADIO CHECK: Bluetooth is ON` if it isn't.

## 2. The private channel (identical on both)

Generate **one** key and set it on **both** radios, at the **same index** (1):

```bash
openssl rand -base64 32          # -> e.g. 3q2+7w...=   keep it secret

meshtastic --ch-add storm-link
meshtastic --ch-index 1 --ch-set psk "base64:3q2+7w...="
meshtastic --ch-index 1 --ch-set uplink_enabled false --ch-set downlink_enabled false
```

Check it with `meshtastic --info`: the `Channels:` list should show `storm-link` at
index 1 on both radios. This key keeps the traffic **private**, but Meshtastic channel
encryption doesn't prove a message is genuine. So every command also carries a signature
made with a separate **link secret** (`/etc/storm/link.secret`, created by the edge
installer). See SECURITY-AUDIT.md F-01 and [KEY_ROTATION.md](KEY_ROTATION.md).
**Never share this channel's URL or QR code.** Guests only ever need the primary channel.

## 3. Edge T-Beam (on the CM4's USB)

```bash
meshtastic --set-owner "Storm Station" --set-owner-short STRM
meshtastic --set device.role CLIENT
meshtastic --set position.gps_mode DISABLED      # saves battery; nothing here needs a fix
meshtastic --set bluetooth.enabled false         # the CM4 talks to it over USB
meshtastic --info | grep -iE 'owner|"id"'        # note the node ID: !xxxxxxxx
```

On the CM4, the radio appears as `/dev/ttyACM0` or `/dev/ttyUSB0`, depending on its
USB-serial chip. Prefer the stable name under `/dev/serial/by-id/` in `edge.toml`.

## 4. Porch T-Beam

The translator Pi reaches the porch radio in one of two ways:

| Link | When | `translator.toml` | Installer |
|---|---|---|---|
| **Wi-Fi / TCP** (default) | The Pi is indoors, away from the porch | `host = "<porch-ip>"` | `install.sh` |
| **USB** | The Pi sits near the porch, with power for both | `port = "/dev/serial/by-id/..."` | `install.sh --usb` |

Either way, set the owner name and role:

```bash
meshtastic --set-owner "Storm Porch" --set-owner-short PRCH
meshtastic --set device.role CLIENT
meshtastic --info | grep -iE 'owner|"id"'        # note the node ID: !xxxxxxxx
```

For a **USB** porch radio, that is all; Bluetooth must already be off (section 1).
For **Wi-Fi / TCP**, also join it to the network. Use a **separate IoT network or VLAN**
with its own password, never the main home Wi-Fi (SECURITY-AUDIT.md F-03). Anyone holding
the board can read that password out of its flash.

```bash
meshtastic --set network.wifi_enabled true \
           --set network.wifi_ssid "YourHomeSSID" \
           --set network.wifi_psk "your-wifi-password"
```

- Turning on Wi-Fi turns off the T-Beam's Bluetooth, which is expected.
- Give it a **DHCP reservation** and put that hostname or IP in `translator.toml` as `host`.
- Check that it's reachable from the translator Pi with `meshtastic --host <porch-ip> --info` (test T1).
- **Its API has no password.** Firewall it so only the translator Pi can reach TCP 4403
  (and its web interface on 80/443). For example, on the IoT VLAN, allow
  `translator-pi -> porch-tbeam:4403` and drop everything else to it.
- **Mount it out of reach** (high under the eaves, in a locked box). If it's ever lost,
  change that Wi-Fi password and rotate the channel key ([KEY_ROTATION.md](KEY_ROTATION.md)).
- **The node accepts one API client at a time.** Once `storm-translator` is running, the
  phone app and the CLI can't connect over the network. Stop the service first:
  `sudo systemctl stop storm-translator`.

## 5. Fill in the node IDs

| File | Key | Value |
|---|---|---|
| `/etc/storm/edge.toml` (CM4) | `radio.porch_node_id` | the porch node's `!xxxxxxxx` |
| `/etc/storm/translator.toml` (translator Pi) | `radio.edge_node_ids` | `["!xxxxxxxx"]` of the edge node |

## Range check (test T8)

From the edge station's final spot, run `storm-bbs --ping` a few times and watch the
porch node's last-heard SNR in the app or `meshtastic --nodes`. Aim for SNR above
about −10 dB. If it's marginal:

- Raise the edge box.
- Use a better 915 MHz antenna on whichever end is worse.
- Add the spare T-Beam as a relay in between, with role `ROUTER` and both channels.
