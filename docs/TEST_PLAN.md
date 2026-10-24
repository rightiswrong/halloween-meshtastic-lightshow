# Test plan

Build up from T0, so each test proves one link and a failure points at one hop.

```
 EDGE (battery, no home network)                    PORCH / HOUSE (trusted LAN)
┌──────────────────────────────────┐   LoRa 915   ┌──────────────────────────────────────────┐
│ Guest phone                      │              │ Porch T-Beam ──Wi-Fi──► LAN              │
│   │ [T5] join hotspot, portal    │   private    │   ▲ [T1] meshtastic --host porch --info  │
│   ▼                              │   PSK chan   │   │                                      │
│ CM4: hostapd + captive portal    │  ─────────►  │ storm-translator (Proxmox VM)            │
│   │ [T6] isolation: no LAN route │   [T3][T4]   │   │ [T2] allowlist only, rejects rest    │
│   ▼                              │              │   ▼                                      │
│ storm-bbs: buttons + Storm Bot   │              │ Home Assistant scripts                   │
│   │ cooldowns                    │              │   │ [T0] baseline, run by hand           │
│   ▼                              │              │   ├─► Gledopto: thunder cue              │
│ Edge T-Beam (USB)                │              │   └─► speaker: thunder clip              │
│   ▲ [T7] mesh users DM "?"       │              │                                          │
└──────────────────────────────────┘              └──────────────────────────────────────────┘
                        [T8] full field test: final location, battery, 30 presses
```

| Test | Setup | Do this | Pass when |
|---|---|---|---|
| **T0** HA baseline | HA only | Run `script.storm_strike` from the HA UI; then `storm-translator --test STRIKE` on the VM | The Gledopto strikes, and the thunder follows 0.4–2.2 s later |
| **T1** Porch radio | Porch T-Beam on Wi-Fi | `meshtastic --host <porch-ip> --info` from the VM (with the service stopped) | Node info returns; region US; `storm-link` at index 1 |
| **T2** Translator | Service running; spare T-Beam on a desk, set as the edge ID | From the spare: `meshtastic --ch-index 1 --sendtext "SB1 00aa STRIKE 80"`; then the same on index 0; then `--sendtext "RM -RF"`; then from a node ID not on the allowlist | Only the first fires and gets `SB1 00aa OK n`. The journal shows the other three dropped |
| **T3** Edge radio, bench | CM4 + edge T-Beam beside the porch | `meshtastic --port /dev/ttyACM0 --info`; then `storm-bbs --ping` (stop the service first) | `translator: PONG` |
| **T4** Bot to light | T3 + translator | `curl -s -H 'Host: 10.66.0.1' -d '{"command":"STRIKE"}' http://10.66.0.1/api/summon` | `"status": "ok"` and a strike within about 3 s |
| **T5** Guest experience | Phone near the CM4 | Join `SummonTheStorm`; tap Strike, Storm, Fire | The portal opens on its own (check iOS and Android); a second tap shows the countdown |
| **T6** Isolation | Phone or laptop on the hotspot | Ping the HA IP and the router; `nmap -p- 10.66.0.1` | No route to the LAN; only 53, 67 and 80 open |
| **T7** Mesh users | A phone with the Meshtastic app on the default channel | DM *Storm Station* `?`, then `1` | Menu arrives; strike fires; a repeat within 30 s is refused with a countdown |
| **T8** Field | Edge at its final spot, on battery | Repeat T4, T5, T7; 30 presses over 10 min; turn show hours off | Every summon answers `ok` or `cooldown`; `CLOSED` works; SNR above about −10 dB; battery use over one hour matches the estimate |

## Before radios exist

- `python3 -m unittest discover -s tests -t .` covers T2, T4, T5 and T7's logic over a simulated mesh.
- `storm-bbs --demo` serves the real page on <http://127.0.0.1:8080/>, with a fake Home Assistant.

## Useful logs

```bash
journalctl -u storm-bbs -f            # edge: "sent SB1 ...", timeouts, radio reconnects
journalctl -u storm-translator -f     # VM: "accepted ...", "dropped (...)", "replied ..."
sqlite3 /var/lib/storm-bbs/storm.db 'select datetime(at,"unixepoch","localtime"),name,command,status,bolts from summons order by id desc limit 20'
```
