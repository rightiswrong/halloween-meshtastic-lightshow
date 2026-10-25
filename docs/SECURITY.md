# Security model

For the full outside-in audit, findings and their status, see
[SECURITY-AUDIT.md](../SECURITY-AUDIT.md).

## Trust boundary

| Untrusted (edge station) | Trusted (home LAN) |
|---|---|
| Open guest hotspot, captive portal, `storm-bbs`, edge T-Beam | Porch T-Beam, `storm-translator` Pi, Home Assistant, lights |

The two sides share **no IP network**. The only link is short text messages on a private
Meshtastic channel.

## What protects the house

1. **No route.** The CM4's Wi-Fi runs only the hotspot. IP forwarding is off, and the
   nftables input policy is *drop*. Guests may reach only DNS, DHCP and port 80, with at
   most 16 connections each. Ethernet allows only SSH, and `ap_isolate` stops guests
   reaching each other. The CM4 holds no home Wi-Fi password and no Home Assistant
   credentials.
2. **Signed, numbered commands.** The channel PSK keeps the traffic private, but
   Meshtastic channel encryption (AES-CTR) doesn't detect tampering or replays.
   So every `SB2` line carries:
   - an HMAC-SHA256 tag made with a separate 32-byte **link secret**;
   - a **sequence number** the translator accepts only once, even across reboots.

   Without the link secret, a recorded command can't be replayed, altered or forged.
   Replies are signed too.
3. **A strict grammar.** The translator accepts only full matches of the `SB2` request
   regex (`protocol.py`): five verbs, a 0–100 level, 48 characters at most, a valid tag
   and a fresh sequence number. Anything else is dropped.
4. **A narrow door into Home Assistant.** By default the translator holds **no token**.
   It calls one local-only webhook, whose automation can only start the four storm
   scripts, with strength clamped to 0–1.
5. **Rate limits everywhere.**
   - The edge has per-guest cooldowns, keyed by MAC address where known.
   - Web and mesh have *separate* overall budgets.
   - Replies to mesh users are throttled.
   - The translator has its own limits, including on PING and on replies to replays, and
     a bounded work queue.
6. **Quiet radios.** Private traffic is sent with a hop limit of 1, so strangers' nodes
   don't relay it across the neighbourhood. Bluetooth is off on both radios, and both
   services warn at startup if it isn't, or if the firmware is older than 2.7.15.
7. **Hardened services.** Each runs as its own user under `ProtectSystem=strict` with
   `NoNewPrivileges`. Task and memory limits are set, and the app is installed by pip
   as the unprivileged user, then made root-owned.

## Worst cases

| If an attacker gets... | They can... |
|---|---|
| A guest phone | Summon within the cooldowns, which is the intended use |
| A LoRa radio in range | Nothing beyond what a mesh user can do. Recorded commands can't be replayed or altered |
| The private-channel PSK | Read the traffic and send unsigned junk, which is dropped. No commands |
| The edge CM4 (root) | Read the link secret and send valid commands, up to the translator's rate limits and show hours. No LAN access, no Home Assistant credentials |
| The translator Pi | The webhook id: the four Halloween effects. In token mode: everything the token can do, which is why webhook mode is the default |
| The porch radio (Wi-Fi mode) | That Wi-Fi network's password: keep it a separate IoT network |

## Operational checklist

- [ ] Both radios: firmware 2.7.15 or newer, Bluetooth off (no `RADIO CHECK` warnings in either journal).
- [ ] Translator in `webhook` mode; no `/etc/storm/ha.token` on the Pi.
- [ ] Porch radio on USB, or on an IoT VLAN that only the translator can reach on TCP 4403.
- [ ] The CM4 has no saved Wi-Fi connections (the installer warns if it does).
- [ ] SSH keys only (`install.sh --harden-ssh`) on both Pis; the edge's Ethernet port unplugged at the edge.
- [ ] Secrets are mode 640: `/etc/storm/link.secret`, `/etc/storm/webhook.id`.
- [ ] The private channel's URL or QR code has never been shared.
- [ ] Test T6 passes: from a guest phone, the Home Assistant IP and the router are unreachable.
- [ ] [Key rotation](KEY_ROTATION.md) after any loss, and after the season.
