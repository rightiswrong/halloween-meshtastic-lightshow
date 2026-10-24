# Radio setup (two LilyGO T-Beams, US 915 MHz)

Both radios run **stock Meshtastic**. Use the [web flasher](https://flasher.meshtastic.org)
and pick your T-Beam's exact board revision. Check that the board is a 915 MHz unit:
T-Beams are also sold as 433 and 868 MHz versions.

The commands below use the `meshtastic` CLI (`pipx install meshtastic`) over USB. Each
`--set` makes the radio reboot, so give it a few seconds between commands.

## 1. Both radios

```bash
meshtastic --set lora.region US
# Keep the default LongFast preset on the primary channel, so ordinary mesh users can DM the bot.
```

## 2. The private channel (identical on both)

Generate **one** key and set it on **both** radios, at the **same index** (1):

```bash
openssl rand -base64 32          # -> e.g. 3q2+7w...=   keep it secret

meshtastic --ch-add storm-link
meshtastic --ch-index 1 --ch-set psk "base64:3q2+7w...="
meshtastic --ch-index 1 --ch-set uplink_enabled false --ch-set downlink_enabled false
```

Check it with `meshtastic --info`: the `Channels:` list should show `storm-link` at
index 1 on both radios. Anyone without this key cannot read or forge `SB1` traffic.
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

## 4. Porch T-Beam (Wi-Fi client on the HA subnet)

```bash
meshtastic --set-owner "Storm Porch" --set-owner-short PRCH
meshtastic --set device.role CLIENT
meshtastic --set network.wifi_enabled true \
           --set network.wifi_ssid "YourHomeSSID" \
           --set network.wifi_psk "your-wifi-password"
meshtastic --info | grep -iE 'owner|"id"'        # note the node ID: !xxxxxxxx
```

- Turning on Wi-Fi turns off the T-Beam's Bluetooth, which is expected.
- Give it a **DHCP reservation** and put that hostname or IP in `translator.toml` as `host`.
- Check that it's reachable from the translator VM with `meshtastic --host <porch-ip> --info` (test T1).
- **The node accepts one API client at a time.** Once `storm-translator` is running, the
  phone app and the CLI can't connect over the network. Stop the service first:
  `sudo systemctl stop storm-translator`.

## 5. Fill in the node IDs

| File | Key | Value |
|---|---|---|
| `/etc/storm/edge.toml` (CM4) | `radio.porch_node_id` | the porch node's `!xxxxxxxx` |
| `/etc/storm/translator.toml` (VM) | `radio.edge_node_ids` | `["!xxxxxxxx"]` of the edge node |

## Range check (test T8)

From the edge station's final spot, run `storm-bbs --ping` a few times and watch the
porch node's last-heard SNR in the app or `meshtastic --nodes`. Aim for SNR above
about −10 dB. If it's marginal:

- Raise the edge box.
- Use a better 915 MHz antenna on whichever end is worse.
- Add the spare T-Beam as a relay in between, with role `ROUTER` and both channels.
