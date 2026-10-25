# Meshtastic Storm BBS — Hardened Design (v1.0.0)

*25 October 2026. A companion to [SECURITY-AUDIT.md](../SECURITY-AUDIT.md), which has the full findings and evidence.*

## Summary

All 21 findings from the outside-in security audit are addressed in this release: 13 fixed, 7 mitigated and 1 partly fixed. CI passes on Python 3.11–3.13, with lint, shellcheck and a dependency vulnerability scan.

The design goal is unchanged: nothing a guest touches has a network path into the home. The hardening closes the gaps the audit found around that boundary:

- **The radio link proves itself.** Every command is signed with a separate link secret and carries a sequence number. Recorded commands can't be replayed or altered, even by someone who knows the channel key.
- **No Home Assistant token on the translator.** It calls one local-only webhook that can only start the four storm scripts.
- **One person can't stop the show for everyone.** Web and radio guests have separate budgets, bot replies are throttled, and the web server has a connection cap.
- **The radios and boxes are locked down.** Bluetooth is off, firmware 2.7.15 or newer is required (both checked at startup), the firewall defaults to drop, and SSH can be keys-only.

None of this has run on the real T-Beams, Pis or Home Assistant yet. Upgrading needs two new secrets on both boxes (see [Upgrade and deployment](#upgrade-and-deployment)).

## Architecture

The shape is unchanged from the earlier design; what changed is what each hop checks.

```
 UNTRUSTED: edge station (battery)                 TRUSTED: home LAN
┌───────────────────────────────────────┐         ┌───────────────────────────────────────┐
│ Guest phone                           │         │  Gledopto            Speaker          │
│   hotspot web page, or a Meshtastic DM│         │  overhead strike     thunder clip     │
│        │                              │         │        ▲                 ▲            │
│        ▼                              │         │ Home Assistant                        │
│ CM4 hotspot + firewall                │         │   storm_webhook: 4 verbs, local only  │
│   DNS, DHCP, :80 only; 16 conns/guest │         │        ▲ webhook (no token)           │
│        │                              │         │ Translator Pi                         │
│        ▼                              │         │   checks tag + sequence               │
│ storm-bbs                             │         │        ▲                              │
│   cooldowns, budgets; signs commands  │         │ Porch T-Beam                          │
│        │                              │ signed  │   USB, or IoT VLAN (TCP 4403)         │
│        ▼                              │  SB2    │        ▲                              │
│ Edge T-Beam ──────────────────────────┼────────►┼────────┘                              │
│   USB; Bluetooth off; hop limit 1     │ private │                                       │
└───────────────────────────────────────┘  PSK    └───────────────────────────────────────┘
```

1. A guest taps a button on the hotspot page, or DMs the edge radio. The firewall and `storm-bbs` apply connection caps, cooldowns and shared budgets.
2. `storm-bbs` sends one signed `SB2` line on the private channel, with a fresh sequence number and a hop limit of 1.
3. The porch radio passes it to the translator Pi, over USB or from an IoT VLAN.
4. The translator checks the sender, the signature and the sequence number, then calls the Home Assistant webhook. It holds no token.
5. The `storm_webhook` automation starts one of four scripts: the Gledopto flashes, then the thunder plays. A signed reply tells the guest how it went.

## The signed link (SB2)

Meshtastic's channel encryption (AES-CTR) keeps traffic private but doesn't detect tampering or replays. So `SB2` adds its own integrity and freshness on top, independent of the radio firmware.

| Direction | Format | Example |
| --- | --- | --- |
| Request, edge to translator | `SB2 <seq> <VERB> [level] <tag>` | `SB2 1042 STRIKE 80 q9Zj5oKkkeY` |
| Reply, translator to edge | `SB2 <seq> <STATUS> [value] <tag>` | `SB2 1042 OK 17 Xk3...` |

- **Tag:** HMAC-SHA256 over the version, direction (`req` or `rep`) and fields, truncated to 64 bits and base64url-encoded (11 characters). It's keyed with a 32-byte **link secret** in `/etc/storm/link.secret` on both boxes, separate from the channel PSK. Because of the direction label, a reply can't be replayed as a request.
- **Sequence numbers:** the edge never reuses one. It reserves blocks of 64 in `seq.json` before using them, so a crash skips numbers rather than repeating them.
- **Replay window:** the translator accepts each number once. It tolerates arrival up to 64 numbers out of order and saves its high-water mark in `link.json`, so a replay after a reboot is still refused. A duplicate it has already answered gets the cached reply resent, and nothing runs again.
- **STALE recovery:** if the edge ever loses its counter (for example, a re-imaged SD card), the translator answers with a signed `STALE <highest>`. The edge skips past that number and retries once.
- **Statuses:** `OK <bolts tonight>`, `CLOSED`, `BUSY`, `ERR`, `PONG` and `STALE <highest>`. Every line is 48 characters or fewer.
- **Bench tool:** `storm-sign --secret-file /etc/storm/link.secret --seq 900001 STRIKE 80` prints a valid line for test T2; `--verify` checks a received one.

## Home Assistant: one narrow door

The translator now reaches Home Assistant only through the `storm_webhook` automation, and holds no token.

- **Why:** Home Assistant tokens can't be limited per service. Any token, even a non-admin user's, can unlock doors or disarm an alarm. A stolen webhook id can only run the four Halloween effects.
- **The automation:** `local_only: true`, POST only. It accepts the verbs `STRIKE`, `STORM`, `FIRE` and `HEART`, clamps `strength` to 0–1, and does nothing while `input_boolean.storm_show_running` is off.
- **The id:** generated by the translator installer into `/etc/storm/webhook.id` (mode 640), and added to Home Assistant's `secrets.yaml` as `storm_webhook_id`.
- **Show hours:** `[show] hours = "18:00-22:00"` on the translator answers guests `CLOSED` outside the schedule. The Home Assistant switch is a second, manual kill switch, which the translator can't see in webhook mode.
- **Debugging:** Home Assistant answers every webhook call with HTTP 200, even ones it ignores. Use the automation's **Traces** to see what happened.
- **Legacy token mode:** `mode = "token"` still works and can read the show switch, but logs a warning at every start. Avoid it.

## Abuse limits and hardening

Every layer now limits what one person can do, so a single prankster can't stop the show for everyone or drain the edge battery.

| Layer | Control | Default |
| --- | --- | --- |
| Edge, web guests | Cooldown per device, keyed on MAC address from dnsmasq's leases | 1 summon per 30 s |
| Edge, shared budgets | Separate for web and mesh, so a spoofed radio flood can't starve the gate | Web 6/min (burst 3); mesh 2/min (burst 2) |
| Edge, bot replies | Per sender, plus an overall budget; silent beyond that | 3, then 1 per 10 s; 20/min overall |
| Edge, DM backlog | Messages waiting for a worker; extras dropped | 8 |
| Edge, web server | Concurrent connections (extras closed without a thread); one summon in flight per guest | 48 |
| Edge, firewall | Per-guest connections and new-connection rate on port 80; input policy drop; Ethernet allows SSH only; IPv4-only hotspot | 16 per guest; 30 new/s |
| Edge, systemd | `TasksMax`, `MemoryMax` | 128 tasks, 256 MB |
| Translator | Own rate limit; PING limit; BUSY, STALE and resent replies throttled; bounded queue | 12/min (burst 3); 6/min; ~1/s; 16 |
| Radios | Private traffic hop limit; startup `RADIO CHECK` for Bluetooth on or firmware below 2.7.15 | 1 hop |

Other hardening:

- **Page:** script and styles moved to separate files. The Content-Security-Policy is now `default-src 'none'` with `'self'` only, no `unsafe-inline`. The page states it never asks for passwords, payment or app installs.
- **Hotspot:** 15-minute DHCP leases, 48 client slots, dead clients dropped. WPA2 with a password on the sign is documented as an option.
- **Privacy:** mesh names are shown as "a mesh visitor", the summon log is kept 7 days, and DHCP hostname logging is off.
- **Install:** pip runs as the unprivileged service user, then the code is made root-owned. `--harden-ssh` sets keys-only SSH, and refuses if you have no key installed.
- **CI:** actions pinned to commit SHAs (Node 24 releases), `pip-audit` in its own job reporting annotations, and `shellcheck`.

## Upgrade and deployment

The old `SB1` protocol is gone, so both boxes, both radios and Home Assistant need updating together.

| Secret | Where it lives | Created by |
| --- | --- | --- |
| Link secret | `/etc/storm/link.secret` on the edge CM4 **and** the translator Pi | Edge installer (`openssl rand -base64 32`); copy it to the translator |
| Webhook id | `/etc/storm/webhook.id` on the translator Pi, and `storm_webhook_id` in HA's `secrets.yaml` | Translator installer (`openssl rand -hex 24`) |
| Channel PSK | Both T-Beams | You, once (unchanged) |

Steps:

- [ ] Radios: update both to firmware 2.7.15 or newer, then `meshtastic --set bluetooth.enabled false` on each
- [ ] Porch radio: USB to the translator Pi, or Wi-Fi on a separate IoT VLAN that only the translator can reach on TCP 4403
- [ ] Edge CM4: `sudo ./deploy/edge/install.sh --harden-ssh`, then edit `/etc/storm/edge.toml`
- [ ] Copy `/etc/storm/link.secret` from the edge to the translator Pi
- [ ] Translator Pi: `sudo ./deploy/translator/install.sh --harden-ssh` (add `--usb` if the porch radio plugs in); edit `/etc/storm/translator.toml`, including `[show] hours`
- [ ] Home Assistant: install the updated `storm.yaml` package and add `storm_webhook_id` to `secrets.yaml`; restart
- [ ] Run `storm-translator --check` and `--test STRIKE`, then reboot the edge and run `storm-bbs --ping` (expect `PONG`)
- [ ] Unplug the edge's Ethernet before it goes out

To rotate any secret later, follow [KEY_ROTATION.md](KEY_ROTATION.md).

## Audit findings and status

Every finding from [SECURITY-AUDIT.md](../SECURITY-AUDIT.md) has a status; the full write-up and evidence are there.

| ID | Finding | Severity | Status | Change |
| --- | --- | --- | --- | --- |
| F-01 | Radio commands replayable or alterable without the key | Medium | Fixed | Signed SB2 with sequence numbers and a persistent replay window |
| F-02 | HA token over-privileged | Medium | Fixed | Webhook mode by default; no token on the translator |
| F-03 | Porch radio holds Wi-Fi password; open API on the LAN | Medium | Mitigated | IoT VLAN, API firewall, USB option, mounting guidance |
| F-04 | Porch radio Bluetooth on (PIN 123456) | Medium | Fixed | Bluetooth off everywhere; startup check |
| F-05 | Spoofed radio IDs dodge cooldowns | Medium | Mitigated | Separate web and mesh budgets |
| F-06 | Bot answers every DM; unbounded queue | Medium | Fixed | Reply throttling; DM backlog of 8 |
| F-07 | One thread per web connection (confirmed) | Medium | Fixed | Cap of 48; firewall per guest; systemd limits. 400 sockets now give 51 threads (was 403) |
| F-08 | Unattended edge box, open Ethernet | Medium | Mitigated | Default-drop firewall, keys-only SSH, key rotation guide |
| F-09 | Open hotspot: evil twin, deauth, slot exhaustion | Low | Mitigated | Page warning, short leases, more slots, optional WPA2 |
| F-10 | Web cooldown bypass by changing IP | Low | Mitigated | Cooldown keyed on MAC address |
| F-11 | No minimum firmware (CVE-2025-53627) | Low | Fixed | 2.7.15 minimum; startup check |
| F-12 | HA token over plain HTTP | Low | Mitigated | Webhook mode sends no token |
| F-13 | Supply chain | Low | Partly fixed | Unprivileged pip, version cap, SHA-pinned actions, pip-audit. Open: hash-locked requirements |
| F-14 | Private traffic relayed across the neighbourhood | Low | Fixed | Hop limit 1 |
| F-15 | Translator replies unthrottled | Low | Fixed | PING limit, reply budget, bounded queue |
| F-16 | BadUSB path via the porch radio's USB cable | Low | Mitigated | Indoor Pi, USBGuard advice |
| F-17 | Privacy: names, logs, DHCP hostnames | Low | Fixed | Names hidden, 7-day retention, `log-dhcp` off |
| F-18 | Forged replies mislead guests | Info | Fixed | Signed replies |
| F-19 | Security docs overstated the PSK | Info | Fixed | `docs/SECURITY.md` rewritten |
| F-20 | CSP `unsafe-inline` | Info | Fixed | Separate script and style files |
| F-21 | Demo exposed on the bench LAN | Info | Fixed | Docs: keep it on `127.0.0.1` |

## Residual risks and next tests

The remaining risks are either inherent to an open, radio-based show or depend on how the hardware is deployed.

- **The edge CM4 holds the link secret.** If it's compromised, an attacker can send valid commands, still within the translator's rate limits and show hours, and without LAN access. That was true before the fix too.
- **Two show schedules.** In webhook mode the translator can't see Home Assistant's show switch. If the switch is off but `[show] hours` say open, guests are told "summoned" and nothing happens. Keep the schedules aligned.
- **Inherent to open radio:** look-alike Wi-Fi networks, deauth floods and LoRa jamming can't be prevented, only made obvious to guests.
- **Physical access:** a stolen radio exposes the channel PSK, and a stolen porch radio in Wi-Fi mode exposes that network's password. Rotate both after any loss.
- **Open item:** a hash-locked requirements file (F-13).

New and changed tests in the [test plan](TEST_PLAN.md):

- [ ] **T0:** `storm-translator --test STRIKE` through the webhook; check the automation's Traces
- [ ] **T2:** a signed line from `storm-sign`; the same line replayed; one with `80` changed to `99`; all but the first must do nothing
- [ ] **T6:** 300 connections from one laptop on the hotspot while a phone still loads the page; only SSH open on Ethernet
- [ ] **T6b:** no `RADIO CHECK` warnings in either journal; no pairable Bluetooth near either radio
