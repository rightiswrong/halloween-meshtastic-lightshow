# Security model

## Trust boundary

| Untrusted (edge station) | Trusted (home LAN) |
|---|---|
| Open guest hotspot, captive portal, `storm-bbs`, edge T-Beam | Porch T-Beam, `storm-translator` VM, Home Assistant, lights |

The two sides share **no IP network**. The only link is the private Meshtastic channel,
which is encrypted with a PSK held by just the two T-Beams.

## What protects the house

1. **No route.** The CM4's Wi-Fi runs only the hotspot. IP forwarding is off, and the
   nftables `forward` chain drops everything. Only DNS, DHCP and port 80 are open to
   guests, and `ap_isolate` stops guests reaching each other. The CM4 holds no home
   Wi-Fi password and no HA token.
2. **The private channel's key.** Without the PSK, nobody can read or inject `SB1`
   traffic. The sender node ID check is a second filter, but it is weak on its own:
   Meshtastic node IDs aren't authenticated.
3. **A strict grammar.** The translator accepts only full matches of the `SB1` request
   regex (`protocol.py`): five verbs, a 0–100 level, 40 characters at most. Anything
   else is logged and dropped without a reply.
4. **A fixed mapping.** Each verb maps to one configured `script.*` entity. Nothing from
   the mesh is passed to Home Assistant except a strength value of 0.00–1.00.
5. **Rate limits on both sides.** The edge applies per-guest cooldowns and a global
   limit. The translator applies its own limit (`per_minute`, `burst`) and answers
   `BUSY`, so even a compromised edge station can't flood Home Assistant.
6. **A hardened service.** The translator runs as its own user under `ProtectSystem=strict`,
   with no capabilities and the token file readable only by that user.

## Worst cases

| If an attacker controls... | They can... |
|---|---|
| A guest phone | Summon within the cooldowns, which is the intended use |
| The edge CM4 (root) | Send allowed commands up to the translator's rate limit, and change what guests see. They gain no LAN access and no HA token |
| The private-channel PSK | The same as the CM4 case: allowed commands, up to the rate limit |
| The translator VM | Everything the HA token can do, which is why the token stays there and nowhere else |

## Operational checklist

- [ ] The CM4 has no saved Wi-Fi connections (the installer warns if it does).
- [ ] SSH over the hotspot is commented out in `storm-firewall.nft` after setup.
- [ ] The private channel's URL or QR code has never been shared.
- [ ] `/etc/storm/ha.token` is mode 640, owned by `root:storm-translator`.
- [ ] Test T6 passes: from a guest phone, the HA IP and the router are unreachable.
