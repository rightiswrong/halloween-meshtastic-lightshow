# Security Audit: Halloween Meshtastic Light Show

| | |
|---|---|
| **Audit date** | 25 October 2026 |
| **Code reviewed** | `main` at commit `74ebeac` ("Host the translator on a Raspberry Pi instead of a Proxmox VM") |
| **Method** | Source and configuration review, threat modelling from the outside in, and targeted local tests of the edge web server. No testing on real radios, Wi-Fi hardware or Home Assistant. |
| **Audience** | Entry- and mid-level security staff. Terms are explained the first time they appear, and in the [glossary](#glossary). |
| **Remediation** | Status updated 25 October 2026, after the fixes. See [section 0](#0-remediation-status). The findings below describe the system **as audited**. |

---

## 0. Remediation status

All 21 findings were addressed: 13 fixed in code or configuration, 7 mitigated, and 1
partly fixed. "Mitigated" means the risk is reduced but some of it is inherent, or
depends on how you deploy. Each fix has a test or check where one is possible. As with
the audit itself, none of this has been run on real radios or a live Home Assistant yet.

| ID | Status | What changed | Verified by |
|---|---|---|---|
| F-01 | **Fixed** | New `SB2` protocol: every line carries an HMAC-SHA256 tag (link secret, separate from the channel PSK) and a sequence number. The translator keeps a replay window whose high-water mark survives reboots. An edge that lost its counter gets a signed `STALE` reply and skips ahead | `test_protocol` (every one-character change rejected; wrong key; reply-as-request); `test_translator` (replay runs once; replay after a reboot refused) |
| F-02 | **Fixed** (default) | Webhook mode is the default: the translator holds **no Home Assistant token**. The `storm_webhook` automation is local-only and POST-only, allows four verbs, and clamps strength. Token mode remains, with a warning at every start | `test_ha` (webhook sends no credentials); `test_config` |
| F-03 | Mitigated | Docs: IoT network or VLAN with its own password, firewall the porch radio's API to the translator only, mount it out of reach, or use USB mode (no Wi-Fi password on the radio) | Operator checklist in `docs/SECURITY.md` |
| F-04 | **Fixed** | Bluetooth off on both radios in every mode (docs). Both services log `RADIO CHECK: Bluetooth is ON` at startup if it isn't | `test_mesh` |
| F-05 | Mitigated | Separate web and mesh budgets (web 6/min, mesh 2/min by default), so a spoofed-identity radio flood can't starve guests at the gate. Mesh users can still be starved by a mesh flood, which is inherent to unauthenticated node IDs | `test_end_to_end.test_mesh_flood_cannot_starve_the_web` |
| F-06 | **Fixed** | Every reply to a mesh user is throttled (3 per sender, then 1 per 10 s; 20/min overall). At most 8 DMs wait for a worker; the rest are dropped silently | `test_end_to_end.test_dm_flood_is_throttled` |
| F-07 | **Fixed** | The app serves at most 48 connections at once and closes extras without a thread. One summon in flight per guest. The firewall caps each guest at 16 connections and rate-limits new ones. systemd `TasksMax=128`, `MemoryMax=256M` | Re-measured: 400 idle sockets from one client now give **51 threads** (was 403), with 352 refused. `test_web.test_connection_cap` |
| F-08 | Mitigated | Edge firewall now defaults to *drop* on every interface; Ethernet allows only SSH. `install.sh --harden-ssh` sets keys-only SSH (refuses if you have no key, so you can't lock yourself out). New `docs/KEY_ROTATION.md`. A physical attacker can still take the radio | `nft -c`; shellcheck in CI |
| F-09 | Mitigated | Page and sign text: "never asks for passwords, payment or app installs". 15-minute leases, 48 client slots, dead clients dropped, optional WPA2 documented. Evil twins and deauth are inherent to open Wi-Fi | `test_web` (page text) |
| F-10 | Mitigated | Web cooldowns are keyed on the MAC address from dnsmasq's lease file, so changing IP by hand no longer resets them. MAC addresses are still spoofable; the web budget caps the total | `test_web.test_lease_table_maps_ip_to_mac` |
| F-11 | **Fixed** | Minimum firmware 2.7.15 documented. Both services log a `RADIO CHECK` warning for older firmware | `test_mesh` |
| F-12 | Mitigated | Webhook mode sends no token, so nothing reusable crosses the LAN. HTTPS is still recommended for token mode | `test_ha` |
| F-13 | Partly fixed | pip now runs as the unprivileged service user (code then made root-owned); `meshtastic>=2.5,<3`; CI actions pinned to commit SHAs; `pip-audit` and `shellcheck` in CI. **Open:** a hash-locked requirements file (it couldn't be generated in this environment) | CI |
| F-14 | **Fixed** | Private-channel traffic is sent with `hop_limit = 1` (configurable) | `test_translator.test_replies_use_the_hop_limit`, `test_end_to_end` |
| F-15 | **Fixed** | PING has its own limit. BUSY, STALE and resent replies share a budget of about 1 per second. The work queue is bounded at 16 | `test_translator` (ping limit, quiet BUSY, bounded queue) |
| F-16 | Mitigated | Docs: keep the translator Pi indoors, consider USBGuard. The `--usb` installer prints a reminder | — |
| F-17 | **Fixed** | Mesh names hidden by default ("a mesh visitor"). Summon log kept 7 days. `log-dhcp` off | `test_end_to_end` (privacy, retention) |
| F-18 | **Fixed** | Replies are signed; forged replies are dropped | `test_protocol`, `test_end_to_end.test_edge_ignores_forged_replies` |
| F-19 | **Fixed** | `docs/SECURITY.md` rewritten to match the real guarantees | — |
| F-20 | **Fixed** (CSP) | Script and styles moved to `/app.js` and `/app.css`; CSP is now `default-src 'none'` with `'self'` only, no `unsafe-inline`. The stdlib server stays, behind the F-07 limits | `test_web`; page checked in a headless browser with no console errors |
| F-21 | **Fixed** | README: demo listens on `127.0.0.1` only; keep it that way | — |

**Residual risks to know about:**
- **The edge CM4 now holds the link secret.** If it's compromised, an attacker can send
  valid commands. They are still limited by the translator's rate limits and show hours,
  and get no LAN access. That was already true before the fix.
- **Webhook mode can't read Home Assistant's show switch.** When
  `input_boolean.storm_show_running` is off but the translator's `[show] hours` say open,
  guests are told "summoned" but nothing happens. Keep both schedules aligned.
- **Upgrading needs a new secret on both boxes.** `SB1` is gone. Run the installers
  again (the edge generates `/etc/storm/link.secret`), copy the secret to the translator,
  and add the webhook id to Home Assistant.

---

## 1. Executive summary

The project lets the public trigger Halloween lighting and sound effects. Guests use an
open Wi-Fi hotspot or the Meshtastic LoRa radio mesh. The design is sound in its main
goal: **nothing a guest touches has a network path into the home**. The guest-facing
box (the "edge station") has no route to the home LAN and holds no Home Assistant
credentials. The only thing that crosses into the house is a short text command on a
radio channel, which the trusted side checks against a strict allowlist.

The audit found **no Critical findings**. Nothing found lets an outsider reach the home
network, read household data, or control anything beyond the four Halloween effects.

The most important issues are these:

1. **The radio link is weaker than the docs claim (F-01, Medium).** Meshtastic's channel
   encryption keeps messages secret but does not prove they are genuine or new. Someone
   in radio range with a cheap LoRa radio can record a command and **replay or alter**
   it later, *without knowing the key*. The impact is limited to triggering the show,
   still subject to rate limits and show hours. But it contradicts the project's own
   security model document.
2. **The Home Assistant token can do far more than the job needs (F-02, Medium, highest
   impact).** Home Assistant tokens can't be limited to specific actions. If the
   translator Pi were ever compromised, the same token could unlock doors, disarm an
   alarm or open a garage, if those are in Home Assistant. A webhook-based design
   removes this risk entirely.
3. **The radios themselves are attack surfaces.** The porch radio in USB mode keeps
   Bluetooth on (F-04). Screenless boards default to the PIN `123456`. The Wi-Fi-mode
   porch radio stores the **home Wi-Fi password** in readable flash and runs an
   unauthenticated admin API on the LAN (F-03).
4. **Several cheap denial-of-service paths** let one person stop the show for everyone.
   They can spoof radio identities to dodge cooldowns (F-05), flood the bot with
   messages it always answers (F-06), or exhaust the web server's threads. That last
   one was confirmed in testing: one client opened 400 connections and got 400 threads
   (F-07).

Most of these are **quick configuration or small code fixes**. A prioritised plan is in
[section 6](#6-remediation-roadmap). Four items are worth doing before 31 October:
F-04, F-11, F-06/F-07 and F-14.

### Findings at a glance

| ID | Title | Severity | Surface | Fix effort |
|---|---|---|---|---|
| F-01 | Radio commands can be replayed or altered without the key | **Medium** | LoRa private channel | Medium (code) |
| F-02 | HA token is over-privileged: a translator compromise gives whole-house control | **Medium** | Trusted side / HA | Medium (HA + code) |
| F-03 | Porch radio stores the home Wi-Fi password and exposes an unauthenticated API | **Medium** | Porch T-Beam | Low (config) |
| F-04 | Porch radio in USB mode leaves Bluetooth on (default PIN 123456) | **Medium** (High if screenless) | Porch T-Beam | Low (config) |
| F-05 | Spoofable radio identities defeat per-guest cooldowns | **Medium** | LoRa public channel | Low–Medium |
| F-06 | Bot answers every DM without limit; work queue is unbounded | **Medium** | LoRa public channel | Low (code) |
| F-07 | Web server: one thread per connection, no cap (confirmed) | **Medium** | Guest Wi-Fi / HTTP | Low (config + code) |
| F-08 | Edge box is unattended: physical access, open Ethernet, extractable radio key | **Medium** | Physical | Low–Medium |
| F-09 | Open hotspot: evil-twin phishing of guests, deauth, DHCP and client-slot exhaustion | **Low** | Guest Wi-Fi | Low |
| F-10 | Per-IP web cooldown is bypassable by changing IP or MAC | **Low** | Guest Wi-Fi / HTTP | Low |
| F-11 | No minimum Meshtastic firmware version (CVE-2025-53627) | **Low** | Radios | Low |
| F-12 | HA token sent over plain HTTP on the LAN | **Low** | Trusted LAN | Low |
| F-13 | Supply chain: unpinned dependencies, root install, unpinned CI actions | **Low** | Build / install | Low |
| F-14 | Private-channel traffic is relayed across the neighbourhood mesh | **Low** | LoRa | Low (code) |
| F-15 | Translator answers PING and BUSY without limit (airtime amplification) | **Low** | LoRa private channel | Low (code) |
| F-16 | USB-attached porch radio is a BadUSB path into the translator Pi | **Low** | Physical / USB | Low |
| F-17 | Privacy: mesh names published, logs kept indefinitely, DHCP hostnames logged | **Low** | Data handling | Low |
| F-18 | Forged replies can show a guest a false result | **Info** | LoRa private channel | (fixed by F-01) |
| F-19 | Security docs overstate what the channel key guarantees | **Info** | Documentation | Low |
| F-20 | Web hardening notes: stdlib HTTP server, CSP `unsafe-inline`, Host check | **Info** | HTTP | — |
| F-21 | `--demo --listen 0.0.0.0` exposes a demo instance on the bench LAN | **Info** | Bench setup | — |

**Severity scale.** Severity combines how likely an attack is with how bad its result
would be. **Critical:** easy to exploit and gives access to the home network or
household systems. **High:** gives that access under realistic conditions. **Medium:**
needs special access or equipment, or the damage is limited to the show, privacy or
availability. **Low:** small impact or unlikely. **Info:** no direct risk; hygiene or
documentation.

---

## 2. Scope and method

**In scope:** everything in this repository, and how it is deployed:

| Component | Where it runs | Code and config |
|---|---|---|
| Guest Wi-Fi hotspot, captive DNS/DHCP, firewall | Edge CM4 | `deploy/edge/*` |
| `storm-bbs`: guest web page, Meshtastic Storm Bot, cooldowns | Edge CM4 | `src/stormshow/bbs/*` |
| Edge T-Beam (stock Meshtastic) | Edge, USB to the CM4 | `docs/RADIO_SETUP.md` |
| `SB1` command protocol | Both radios | `src/stormshow/protocol.py` |
| Porch T-Beam (stock Meshtastic) | Porch, on Wi-Fi or USB | `docs/RADIO_SETUP.md` |
| `storm-translator`: allowlist and Home Assistant client | Translator Raspberry Pi | `src/stormshow/translator/*`, `deploy/translator/*` |
| Home Assistant package (four scripts, show hours) | Home Assistant | `homeassistant/packages/storm.yaml` |
| Build and install | GitHub Actions, `install.sh` | `.github/`, `pyproject.toml` |

**Out of scope:** Home Assistant itself, the Gledopto and Spooky Sound firmware, the home
router and Wi-Fi, and Meshtastic firmware internals. These are referenced where they
affect this project.

**How the audit was done:**
1. Map every way an outsider can interact with the system (section 3), from the street inward.
2. For each surface, list who the attacker is, what they need, and what they could gain.
3. Review the code and configuration for each path.
4. Check claims against primary sources, mainly the Meshtastic documentation on
   encryption limits and Bluetooth, and the Meshtastic firmware advisories.
5. Confirm selected issues with local tests. F-07 was reproduced against the real web
   server code.

**Limitations:** no live radio, Wi-Fi or Home Assistant testing was possible, so
radio-level findings rely on Meshtastic's documented behaviour. This audit was performed
by the same assistant that wrote the code. An independent review by a second person is
recommended before relying on it for anything beyond a seasonal display.

---

## 3. System and attack-surface map

```
 OUTSIDE (street, neighbours, anyone in radio range)
 ─────────────────────────────────────────────────────────────────────────────────────
  [A] LoRa 915 MHz radio ── public channel: anyone with a Meshtastic radio
                        └── private channel: holders of the PSK (and see F-01)
  [B] Open Wi-Fi "SummonTheStorm": anyone within roughly 50 m
  [C] Physical: the edge box at the property edge, and the porch radio
 ─────────────────────────────────────────────────────────────────────────────────────
 UNTRUSTED ZONE: edge station (battery)
   Edge CM4: hostapd (Wi-Fi AP), dnsmasq (DNS/DHCP), nftables, storm-bbs (HTTP :80)
   Edge T-Beam (USB): holds the private-channel key
 ─────────────────────────── trust boundary: radio only, no IP ───────────────────────
 TRUSTED ZONE: home LAN
   Porch T-Beam: Wi-Fi client with TCP API :4403 and web UI, OR USB to the translator Pi
   Translator Pi: storm-translator, holds the Home Assistant token
   Home Assistant: four storm scripts, plus everything else in the house
 ─────────────────────────────────────────────────────────────────────────────────────
  [D] Supply chain: PyPI packages, GitHub Actions, install scripts run as root
```

**Attackers considered:**

| Attacker | Example | Capabilities |
|---|---|---|
| Curious guest | A visitor with a phone | Uses the web page and the Meshtastic app normally, and tries obvious tricks |
| Prankster nearby | A teenager with a $30 LoRa board and a laptop | Can transmit on 915 MHz, spoof node IDs, flood, and run Wi-Fi tools such as deauth or a fake access point |
| Opportunistic thief | Someone who walks past the edge box | Physical access for a few minutes; can unplug and take hardware |
| LAN foothold | A compromised smart plug or TV on the home network | Can reach LAN services such as the porch radio's API |
| Supply-chain attacker | A malicious package update on PyPI | Code runs during install or at runtime |

**Assets, ranked:** (1) the home LAN and household systems controlled by Home Assistant,
such as locks, alarm, cameras and garage; (2) the home Wi-Fi password; (3) the
private-channel key; (4) the availability of the show; (5) guests' safety and privacy.

---

## 4. Findings

Each finding lists the attack surface, what is wrong and why it matters, a realistic
attack, the evidence, and the recommended fix with a way to verify it.

### F-01: Radio commands can be replayed or altered without the key

**Severity:** Medium · **Surface:** [A] LoRa private channel

**What's wrong.** The edge station sends commands such as `SB1 1a2b STRIKE 80` on a
private Meshtastic channel protected by a **PSK** (pre-shared key, a password both
radios know). Meshtastic encrypts channel messages with **AES-CTR**. Meshtastic's
documentation states plainly that this "does not include authentication". The
encryption hides what a message says, but it does not prove the message is genuine or
recent. Two attacks follow:

- **Replay.** An attacker records a real command off the air and transmits it again
  later. Meshtastic radios ignore duplicates only for a short time, and the record is
  cleared when a radio reboots. The translator remembers request IDs for only 120 seconds
  (`DEDUP_WINDOW_S` in `translator/service.py`, line 38). After that, the replayed
  command runs again.
- **Bit-flipping.** CTR mode encrypts by XOR-ing the text with a keystream. Our command
  format is very predictable, so an attacker who guesses the plaintext can flip
  specific bits to change it: for example, change the request ID to get past
  de-duplication, or `STRIKE 80` to `STRIKE 99`. They still don't need the key.

The sender's node ID travels unencrypted and unauthenticated, so the translator's check
that the sender is the edge node does not stop either attack.

**Attack scenario.** A prankster in radio range records one legitimate strike during the
evening. Later, they replay it, or a modified version, whenever they like, triggering
lightning and thunder. Each replay still has to pass the translator's rate limit (12 per
minute) and show hours.

**Impact.** Limited to the show: unwanted flashes and thunder, and possibly annoying
neighbours late at night if show hours are disabled. There is **no** path to the home
network. The finding matters mainly because the security model assumes "only the edge
can command the translator", and that assumption is false.

**Evidence.** `protocol.py` defines the request format with no integrity field.
`translator/service.py`, lines 106–144, trusts any well-formed request from an
allowlisted ID. `docs/SECURITY.md` line 18 claims nobody can inject commands without
the PSK.

**Recommendation.** Add application-level integrity and freshness, independent of
Meshtastic:

1. A **separate 32-byte shared secret** in both `edge.toml` and `translator.toml`, not
   the channel PSK.
2. A **monotonic counter** (a number that only goes up), kept across restarts on the
   edge in SQLite and on the translator in its state file. The translator rejects any
   counter value at or below the last one it accepted.
3. An **HMAC** (a keyed checksum) over `version | counter | verb | level`, truncated to
   about 64 bits and base64-encoded. For example: `SB2 1042 STRIKE 80 q8Zt0rB1xYk`,
   which is still under 40 characters.
4. Sign replies the same way, which also fixes F-18.

Alternative: use Meshtastic **PKI direct messages** (AES-CCM, which is authenticated)
between the two radios, and have the translator require `pkiEncrypted` and a pinned
public key. That relies on firmware behaviour (trust on first use; see F-11) and is
harder to test than an HMAC.

**Verify.** A unit test that replays a captured `SB2` line, and one that changes any
character of it, must both be rejected. In the field, re-send an earlier command from a
spare radio: it must be dropped with "stale counter" or "bad MAC" in the journal.

---

### F-02: HA token is over-privileged; a translator compromise gives whole-house control

**Severity:** Medium (likelihood low, impact high) · **Surface:** trusted side, Home Assistant

**What's wrong.** The translator calls Home Assistant with a **long-lived access token**.
Home Assistant has **no per-service permissions** for tokens. Even a non-admin user's
token can call any service: `lock.unlock`, `alarm_control_panel.alarm_disarm`,
`cover.open_cover` and so on. The translator's code restricts itself to four scripts,
but that is a promise made by the code, not a limit enforced by Home Assistant.

**Attack scenario.** The translator Pi is compromised: an unpatched SSH, a reused
password, a malicious package update (F-13), or physical access (F-16). The attacker
reads `/etc/storm/ha.token` and uses it to unlock the front door or disable cameras.

**Impact.** Potentially the whole smart home, which is the project's most valuable asset.
The likelihood is low, because it needs a trusted-zone compromise first, but nothing in
this project limits the damage.

**Evidence.** `translator/ha.py` sends `Authorization: Bearer <token>` to `/api/...`.
`docs/HOME_ASSISTANT.md` already warns about this, but there is no technical mitigation.

**Recommendation.** Replace the token with a **Home Assistant webhook trigger**:

1. Create one automation with a `webhook` trigger. Use a long random `webhook_id`,
   `local_only: true` (accept only from the LAN) and `allowed_methods: [POST]`.
2. The automation checks that the payload's `verb` is one of `STRIKE`, `STORM`, `FIRE`
   or `HEART`, clamps `strength` to 0–1, and calls the matching script. Everything else
   is ignored.
3. The translator POSTs `{"verb": ..., "strength": ...}` to `/api/webhook/<id>` and holds
   **no token at all**.

A stolen webhook ID can then only do what the webhook allows: the four Halloween
effects. Show hours move into the automation. The bolt counter can stay on the
translator, or move to an HA `counter` entity.

If the token must stay for now: use a dedicated non-admin user, keep the file at mode
`640`, put the translator Pi on a VLAN that can reach only Home Assistant on port 8123
and the porch radio, and revoke the token after the season.

**Verify.** Use the old token, or no token, to call `lock.unlock` from the translator Pi:
it must fail. A POST to the webhook with `{"verb":"UNLOCK"}` must do nothing.

---

### F-03: Porch radio stores the home Wi-Fi password and exposes an unauthenticated API

**Severity:** Medium (High if the porch radio is reachable by the public) · **Surface:** [C] physical, LAN

**What's wrong.** In the default Wi-Fi/TCP mode, the porch T-Beam joins the **home Wi-Fi**.
That creates two problems:

- **The password sits in the radio's flash.** ESP32 boards ship without flash
  encryption, so anyone holding the board can read the flash over USB with
  `esptool.py read_flash` and recover both the **home Wi-Fi password** and the
  **private-channel PSK**. A porch is often reachable from the street.
- **Unauthenticated admin interfaces on the LAN.** The Meshtastic TCP API (port 4403)
  and the built-in web interface accept connections from any device on the LAN, with
  no password. Through them, a device can read the channel keys, change the radio's
  configuration, or reboot it.

**Attack scenarios.**
1. A thief unscrews the porch radio, dumps its flash, and joins the home Wi-Fi from the
   street, ending up **inside the trusted LAN**.
2. A compromised smart-home device on the LAN connects to port 4403 first. Because the
   radio accepts one client at a time, this blocks the translator (denial of service).
   The device can also read the PSK and use it with any LoRa radio.

**Evidence.** `docs/RADIO_SETUP.md` section 4 (Wi-Fi option); `config/translator.example.toml` `host =`.

**Recommendation (pick one):**
- **Prefer USB mode** where practical: the radio then holds no Wi-Fi credentials. Combine
  this with the F-04 and F-16 fixes.
- If Wi-Fi is needed, put the porch radio on a **separate IoT network or VLAN**, with a
  firewall rule that lets only the translator Pi reach it on TCP 4403. Use a Wi-Fi
  network whose password differs from the main home network's.
- Mount the porch radio **out of reach** in a locked or tamper-evident enclosure, and
  rotate the Wi-Fi password if it is ever lost or stolen.

**Verify.** From another LAN device, `nc -vz <porch-ip> 4403` must be refused. Check the
Wi-Fi password the porch radio uses is not the main network's.

---

### F-04: Porch radio in USB mode leaves Bluetooth on (default PIN 123456)

**Severity:** Medium (High for screenless boards) · **Surface:** [A]/[C] Bluetooth, about 10–30 m

**What's wrong.** The radio setup guide disables Bluetooth on the **edge** radio. On the
porch radio, Bluetooth is only switched off as a side effect of joining Wi-Fi. In the
newer **USB** mode the porch radio has no Wi-Fi, so **Bluetooth stays on**. Meshtastic's
documentation says devices **without a screen default to `FIXED_PIN` with PIN
`123456`**. Many T-Beams are sold without the optional OLED screen.

**Attack scenario.** Someone on the pavement opens the Meshtastic phone app, pairs with
"Storm Porch" using `123456`, and gets the full client API. They can read the private
channel's PSK, change settings, and send messages *as the porch radio*.

**Impact.** Loss of the private-channel key, the same as F-01 but worse, plus control of
a trusted-side radio.

**Evidence.** `docs/RADIO_SETUP.md` line 39 disables Bluetooth only for the edge radio.
Section 4's USB path doesn't mention Bluetooth.

**Recommendation.** For **both** radios, in every mode: run `meshtastic --set bluetooth.enabled false`.
If Bluetooth is ever needed, set `bluetooth.mode RANDOM_PIN` (needs a screen) or a
non-default `fixed_pin`. Add this to the radio setup guide and to the test plan.

**Verify.** `meshtastic --get bluetooth` on each radio shows `enabled: false`. A phone
scan near the porch shows no Meshtastic device.

---

### F-05: Spoofable radio identities defeat per-guest cooldowns

**Severity:** Medium · **Surface:** [A] LoRa public channel

**What's wrong.** Cooldowns for mesh users are tracked per **node ID**
(`mesh:<id>`, `bbs/service.py` lines 108 and 204). Node IDs are not authenticated on
public-channel traffic, so a modified radio or a script can claim a new ID for every
message.

**Attack scenario.** A prankster sends `STRIKE` from 1,000 made-up node IDs. Each ID
passes its own cooldown. The only limit left is the **global** bucket (6 per minute,
burst 3), which the attacker now consumes entirely. Legitimate guests then see "The
clouds are recharging" all night.

**Impact.** Availability of the show, and fairness. No access is gained.

**Recommendation.**
- Keep the global limit, but **reserve part of it for the web page**, or track web and
  mesh in separate global buckets, so a radio flood can't starve the guests at the
  gate (and vice versa).
- Add a **mesh-only global limit**, for example 2 per minute.
- Track the number of distinct mesh IDs seen per minute; above a threshold, briefly
  pause mesh summons and log an alert.
- Optionally accept mesh commands only from nodes whose NodeInfo (public key) the edge
  has already heard. That raises the bar, but Meshtastic trusts keys on first use
  (TOFU), so it doesn't eliminate the problem.

**Verify.** A test sending 100 DMs from 100 IDs must leave web-guest summons working.

---

### F-06: Bot answers every DM without limit; work queue is unbounded

**Severity:** Medium · **Surface:** [A] LoRa public channel

**What's wrong.** Only *summons* are rate-limited. Menu (`?`), `WHO` and **unknown
commands** are answered every time (`bbs/service.py` lines 100–106, before the limiter
at line 108). Each DM is handed to a thread pool whose **queue has no size limit**
(line 86, `ThreadPoolExecutor`), and every answer is a radio transmission.

**Attack scenario.** Combined with ID spoofing (F-05), an attacker sends a stream of
`?` DMs. The edge radio spends its airtime answering, real commands and replies are
delayed or lost, and the edge battery drains faster. The pending queue grows in memory.

**Recommendation.**
- Apply a per-sender limit to **all** replies, and stay silent on unknown commands after
  the first, or always.
- Add a **global reply budget**, for example 20 per minute, and drop silently when it's
  exceeded.
- Bound the DM queue: track pending DMs with a counter, and drop new ones when more than
  about 8 are waiting.

**Verify.** A test sending 500 `?` DMs must cause at most the budgeted number of replies
and no memory growth.

---

### F-07: Web server uses one thread per connection with no cap (confirmed)

**Severity:** Medium · **Surface:** [B] guest Wi-Fi to HTTP port 80

**What's wrong.** The portal uses Python's `ThreadingHTTPServer`
(`bbs/web.py` line 119), which starts **one thread per connection with no upper limit**.
Each `/api/summon` request can hold its thread for up to 25 s while waiting for the
radio. The 15-second idle timeout (line 41) applies per read, so a client that sends one
byte every few seconds ("slowloris") keeps its threads alive indefinitely. Python's own
documentation describes `http.server` as not recommended for production.

**Evidence (tested).** A local test opened 400 idle connections from **one** client: the
server went from 3 to **403 threads**. They fell back after the 15 s timeout only
because the test sockets went silent. Each thread costs memory on a 1–4 GB CM4, and
thousands of threads can exhaust it or hit process limits, taking the page offline.

**Recommendation.**
- **Firewall:** limit connections per guest in `storm-firewall.nft`, for example
  `tcp dport 80 ct count over 16 drop` per source address (using an nftables meter or
  set), plus a new-connection rate limit.
- **Code:** cap concurrent handlers with a semaphore of 32–64, returning a busy response
  when full. Allow only one in-flight summon per client IP. Set an absolute
  per-request deadline of about 30 s, not just an idle timeout.
- **systemd:** set `TasksMax=128` and `MemoryMax=256M` on `storm-bbs.service`, so a
  flood can't take down the whole box.
- Optionally, put a small hardened server such as `nginx` in front of the Python app.

**Verify.** Repeat the 400-socket test: the thread count stays under the cap, and a
second phone can still load the page.

---

### F-08: Edge box is unattended: physical access, open Ethernet, extractable radio key

**Severity:** Medium · **Surface:** [C] physical

**What's wrong.** The edge station sits at the property edge, on battery, all evening.

- **Edge T-Beam:** like the porch radio, its flash is readable over USB, which exposes
  the **private-channel PSK**. The repository has **no documented procedure to rotate
  the PSK** (and, after F-01, the HMAC secret) if it's stolen.
- **The CM4's firewall applies only to Wi-Fi.** `storm-firewall.nft` line 11 accepts
  everything on any interface other than `wlan0`. Plugging a laptop into the CM4's
  Ethernet port gives full access to SSH and any other listener.
- **Console and storage:** a keyboard and HDMI give a login prompt. The eMMC or SD card
  is unencrypted. It holds no household secrets, but it does hold the summons log (F-17).

**Attack scenario.** Someone opens the box, plugs into Ethernet, tries default or
weak SSH passwords, then reads the edge radio's PSK through the serial API. Or they
simply take the radio.

**Recommendation.**
- Use a **locked or tamper-evident enclosure**, mounted out of easy reach, with the
  antenna outside.
- Firewall Ethernet as strictly as Wi-Fi, or disable `eth0` at the edge, and allow SSH
  only with **keys**: `PasswordAuthentication no`, and no default `pi` user.
- Document **key rotation**: generate a new PSK (and HMAC secret), set it on both
  radios, update the configs. Do this after any loss, and once a year.
- After the season, wipe the radios with `meshtastic --factory-reset`, or re-key them.

**Verify.** With a laptop on the CM4's Ethernet, `nmap` shows only the ports you
intended. Password SSH login is refused.

---

### F-09: Open hotspot: evil-twin phishing, deauth, DHCP and client-slot exhaustion

**Severity:** Low · **Surface:** [B] guest Wi-Fi

**What's wrong.** The hotspot is deliberately open (`hostapd.conf` line 15, `wpa=0`),
so guests can join with one tap. Open networks allow:

- **Evil twin.** An attacker broadcasts the same network name with their own captive
  page asking for an email address, a password or an app install. Guests can't tell the
  difference. This harms the guests, and your reputation.
- **Deauthentication floods.** Without protected management frames, guests can be
  kicked off repeatedly.
- **Resource exhaustion.** `max_num_sta=32` (line 18) and 231 DHCP leases are easy to
  fill with fake clients, locking out real guests.

Client isolation (`ap_isolate=1`) is correctly enabled, so guests can't attack each
other through the access point.

**Recommendation.**
- Add a sign, and a line on the page: **"This page never asks for passwords, payment or
  app installs."**
- Consider **WPA2 with a password printed on the sign** (or WPA3, if your guests'
  phones support it). That stops casual eavesdropping and makes evil twins slightly
  harder.
- Shorten DHCP leases to 15 minutes, and raise `max_num_sta` if the CM4 copes.
- Accept deauth floods and radio jamming (Wi-Fi or LoRa) as unavoidable for any
  open radio service. The show simply stops until the interference does.

---

### F-10: Per-IP web cooldown is bypassable

**Severity:** Low · **Surface:** [B]/HTTP

**What's wrong.** Web cooldowns are keyed on the client's IP (`bbs/web.py` line 112).
On the hotspot, a client can set any free `10.66.0.x` address by hand, or change its MAC
address to get a new DHCP lease, and start a fresh cooldown each time.

**Impact.** As with F-05, the global limit still caps the total, so the harm is
unfairness and denial of service, not extra strikes beyond the limit.

**Recommendation.** Key cooldowns on the **MAC address** taken from the DHCP lease table
(still spoofable, but more work). Configure dnsmasq for static-lease enforcement, or drop
traffic from addresses that dnsmasq didn't hand out. Apply the F-05 fairness split
between web and mesh.

---

### F-11: No minimum Meshtastic firmware version (CVE-2025-53627)

**Severity:** Low · **Surface:** radios

**What's wrong.** The docs say "stock Meshtastic" without a minimum version. Firmware
before **2.7.15** is affected by **CVE-2025-53627** (GHSA-377p-prwp-4hwf, CVSS 5.3): it
silently accepts forged direct messages that look PKI-encrypted. This project doesn't
rely on PKI DMs today, but the F-01 alternative would, and old firmware has other fixed
bugs.

**Recommendation.** State a minimum version (2.7.15 or newer) in `docs/RADIO_SETUP.md`.
Have `storm-bbs` and `storm-translator` log the radio's firmware version at startup and
warn below the minimum. Update both radios before the event.

---

### F-12: HA token sent over plain HTTP on the LAN

**Severity:** Low · **Surface:** trusted LAN

**What's wrong.** The example config uses `http://homeassistant.lan:8123`
(`config/translator.example.toml` line 18). The bearer token travels unencrypted, so any
device that can see the traffic, or spoof the HA host's address on the LAN (ARP
spoofing), can capture it. Combined with F-02, a captured token means whole-house
control.

**Recommendation.** Use HTTPS to Home Assistant with `verify_tls = true` (and `ca_file`
for a private CA), or put the translator Pi on a cable directly to the HA host. Adopting
the F-02 webhook design removes the token entirely.

---

### F-13: Supply chain: unpinned dependencies, root install, unpinned CI actions

**Severity:** Low · **Surface:** [D] build and install

**What's wrong.**
- `pyproject.toml` line 11 requires `meshtastic>=2.5` with no upper bound or hashes. That
  package pulls in many others: protobuf, pyserial, bleak, requests, pyyaml and more.
  A malicious or broken release would be installed automatically.
- `install.sh` runs `pip install` as **root** (into a venv), so code in a package's
  build step runs as root.
- GitHub Actions are referenced by tag (`actions/checkout@v4`), not by commit SHA.
- CI doesn't scan for known-vulnerable dependencies.

**Recommendation.**
- Ship a **lock file** with hashes (`pip-compile --generate-hashes`) and install with
  `pip install --require-hashes -r requirements.lock`.
- Install as an unprivileged user that owns `/opt/storm-*`, then hand ownership to root.
- Pin actions to commit SHAs, and add `pip-audit` to CI.
- After the season, check the radios and Pis for unexpected changes before reuse.

---

### F-14: Private-channel traffic is relayed across the neighbourhood mesh

**Severity:** Low · **Surface:** [A] LoRa

**What's wrong.** `MeshLink.send_text` (`mesh.py` line 138) uses the default hop limit
(3). Meshtastic routers rebroadcast packets they can't decrypt, so every `SB1` command
can be repeated by strangers' nodes up to three hops away. That widens the area in which
an attacker can record packets for F-01, uses neighbours' airtime, and leaks metadata:
how often the show is used, and when.

**Recommendation.** Send `SB1` traffic with `hopLimit=0` or `1`, which needs the edge and
porch radios to hear each other directly or through your own relay, and give your own
relay the private channel. Keep the edge-to-porch link short, with good antennas.

---

### F-15: Translator answers PING and BUSY without limit (airtime amplification)

**Severity:** Low · **Surface:** LoRa private channel

**What's wrong.** In `translator/service.py`, `PING` bypasses the rate limiter
(lines 133–135), and every over-limit request gets a `BUSY` reply (lines 137–141). Both
are queued without limit and transmitted. With the PSK, or through the F-01 replay and
alteration, an attacker can make the porch radio transmit continuously, crowding out
real traffic.

**Recommendation.** Rate-limit PING. Send at most one BUSY per second, and drop silently
beyond that. Bound the translator's work queue.

---

### F-16: USB-attached porch radio is a BadUSB path into the translator Pi

**Severity:** Low · **Surface:** [C] physical, USB

**What's wrong.** In USB mode, a cable runs from the porch radio to the translator Pi on
the trusted side. If the radio is reachable, an attacker can swap it for a **BadUSB**
device: a gadget that pretends to be a keyboard or network adapter and types commands or
redirects traffic.

**Recommendation.** Keep the Pi indoors and run only a cable outside. Install
**USBGuard** to allow only the radio's vendor and product ID. Disable getty on unused
consoles. Prefer a powered hub or isolator in an indoor enclosure.

---

### F-17: Privacy: mesh names published, logs kept indefinitely, DHCP hostnames logged

**Severity:** Low · **Surface:** data handling

**What's wrong.**
- `WHO` and `/api/status` show the **last summoner's Meshtastic long name** to anyone,
  on the web page and on the mesh. People often use real names or callsigns.
- The edge's SQLite log (`bbs/store.py`) keeps every summon (time, IP or node ID, name,
  command) **forever**.
- dnsmasq's `log-dhcp` (`dnsmasq-storm.conf` line 15) logs guests' device hostnames
  (often "Jane's iPhone") and MAC addresses to the journal.

**Recommendation.** Show only "a mesh visitor" or the radio's short name, unless the user
opts in. Delete summons older than 7 days. Remove `log-dhcp` after testing. Mention data
handling on the page.

---

### F-18: Forged replies can show a guest a false result

**Severity:** Info · **Surface:** LoRa private channel

The edge accepts any well-formed reply that claims to come from the porch node and
matches a pending 16-bit request ID (`bbs/service.py`, `on_packet`). An attacker with the
PSK, or using F-01 techniques, could make guests see "Bolt #999 summoned!" when nothing
happened. The impact is cosmetic. **Fixed by** signing replies (F-01).

### F-19: Security docs overstate what the channel key guarantees

**Severity:** Info · **Surface:** documentation

`docs/SECURITY.md` line 18 says "Without the PSK, nobody can read or inject `SB1`
traffic." Reading is correct. Injection by replay or bit-flipping is possible (F-01).
Update the document now, before F-01 is fixed, so operators don't over-trust the
channel.

### F-20: Web hardening notes

**Severity:** Info · **Surface:** HTTP

- **Stdlib server:** `http.server` is minimal. It does a good job here (strict body
  limits, JSON parsing in a try block, no file serving), but see F-07 for its limits
  under load.
- **CSP `unsafe-inline`:** needed for the single-file page. All dynamic text goes through
  `textContent`, and the title is HTML-escaped, so no XSS sink was found. Moving the
  script to a separate file would let the CSP drop `unsafe-inline`.
- **Host-header check:** `_on_portal()` is a routing aid for the captive portal, not a
  security control, since clients set the Host header themselves. That's acceptable,
  because no other origin is reachable from the hotspot.
- **Positive:** parameterised SQL (no SQL injection), no reflection of user input in
  HTML, restrictive response headers (`nosniff`, `no-referrer`, `frame-ancestors 'none'`).

### F-21: Demo mode on the bench

**Severity:** Info · **Surface:** bench LAN

The README suggests `storm-bbs --demo --listen 0.0.0.0` for bench testing. That exposes
the demo page to the whole LAN. It's harmless (a fake Home Assistant), but bind to
`127.0.0.1` or the bench interface, and stop it after testing.

---

## 5. What's already done well

These controls were checked and work as intended. Keep them.

| Control | Where | Why it matters |
|---|---|---|
| **No IP path from guests to the home** | Hotspot not bridged; `ip_forward=0`; nftables `forward` policy drop | The core design goal. Even a full compromise of the edge box doesn't reach the LAN |
| **No household secrets on the edge** | `edge.toml` has no tokens; the installer warns about saved Wi-Fi | Limits the value of stealing or hacking the edge box |
| **Strict command grammar** | `protocol.py`: full regex match, 5 verbs, 0–100 level, 40-char cap; about 25 hostile inputs tested | The trusted side can't be talked into anything else |
| **Fixed verb-to-script mapping** | Translator config validates `script.<name>` | Nothing from the mesh is passed to Home Assistant as a name or template |
| **Rate limits on both sides** | Edge per-guest + global; translator's own bucket | A compromised edge still can't flood Home Assistant |
| **Guest isolation** | `ap_isolate=1`; guest firewall allows only DNS, DHCP, HTTP | Guests can't attack each other or other services on the CM4 |
| **Hardened services** | systemd `ProtectSystem=strict`, `NoNewPrivileges`, dedicated users, no capabilities on the translator | Limits damage from a code bug |
| **Token file permissions** | `640 root:storm-translator`, created by the installer | Other local users can't read the token |
| **Good test coverage of abuse cases** | 45 tests, including wrong channel, unknown sender, duplicates, forged replies, bad HTTP | Regressions get caught |

---

## 6. Remediation roadmap

### Before 31 October (configuration and small changes, about 1–2 hours)

| Order | Action | Fixes |
|---|---|---|
| 1 | Turn Bluetooth off on **both** radios; update `RADIO_SETUP.md` | F-04 |
| 2 | Update both radios to firmware 2.7.15 or newer | F-11 |
| 3 | Porch radio: USB mode, or an IoT VLAN with a firewall rule; mount it out of reach | F-03, F-16 |
| 4 | Edge: firewall `eth0` too, SSH keys only, locked enclosure | F-08 |
| 5 | `storm-bbs.service`: add `TasksMax=128`, `MemoryMax=256M`; nftables per-IP connection cap on port 80 | F-07 |
| 6 | Keep show hours enabled (`show_entity`) so replays at 3 a.m. are answered `CLOSED` | F-01 (reduces impact) |
| 7 | Correct `docs/SECURITY.md` line 18 | F-19 |
| 8 | Add the "never asks for passwords" line to the page and sign | F-09 |

### Short term (code, about a day)

| Action | Fixes |
|---|---|
| Throttle all bot replies; bound the DM and translator queues; rate-limit PING/BUSY | F-06, F-15 |
| Concurrency cap and one in-flight summon per client in the web server | F-07 |
| Separate web and mesh global buckets; mesh-only limit | F-05, F-10 |
| `hopLimit` 0–1 for `SB1` traffic | F-14 |
| `SB2` protocol with HMAC, monotonic counter and signed replies | F-01, F-18 |

### Medium term

| Action | Fixes |
|---|---|
| Replace the HA token with a webhook automation | F-02, F-12 |
| Lock file with hashes, `pip-audit`, SHA-pinned actions, non-root install | F-13 |
| Log retention, name redaction, remove `log-dhcp` | F-17 |
| Documented key-rotation procedure and post-season wipe | F-08 |

---

## 7. How to re-test

After fixes, re-run these as part of `docs/TEST_PLAN.md`:

1. **Replay:** record an `SB2` command with a spare radio, replay it after 5 minutes and
   after a porch-radio reboot. Expected: rejected.
2. **Bluetooth:** scan near both radios with the Meshtastic app. Expected: nothing to pair.
3. **Connection flood:** from one laptop on the hotspot, open 500 connections to port 80
   (for example with `slowhttptest`). Expected: the page still loads on a second phone,
   and the CM4's thread count stays under the cap.
4. **Identity flood:** send 200 `?` DMs from spoofed IDs. Expected: replies capped,
   web summons unaffected.
5. **Isolation:** from the hotspot and from the CM4's Ethernet port, scan the CM4 and
   try to reach the home LAN. Expected: only the intended ports, no LAN.
6. **Token scope:** from the translator Pi, try calling a non-storm service.
   Expected: impossible (after F-02).

---

## Glossary

| Term | Meaning |
|---|---|
| **AES-CTR** | A way of using AES encryption that turns it into a stream of random-looking bytes XOR-ed with the message. It hides content, but on its own doesn't detect tampering |
| **AES-CCM** | AES with a built-in integrity check: tampered messages are rejected |
| **BadUSB** | A USB device that pretends to be something else (often a keyboard) in order to run commands |
| **Bit-flipping** | Changing encrypted bytes so that specific bits of the decrypted message change, without knowing the key. Possible when the encryption has no integrity check |
| **Captive portal** | The page a phone opens automatically after joining a Wi-Fi network |
| **Deauth attack** | Sending forged Wi-Fi "disconnect" frames to kick devices off a network |
| **DoS** | Denial of service: stopping something from working for legitimate users |
| **Evil twin** | A fake Wi-Fi access point with the same name as a real one |
| **HMAC** | A short code computed from a message and a secret key. Without the key, you can't make a valid one or change the message unnoticed |
| **Monotonic counter** | A number that only increases. Each message carries the next value, so old messages (replays) can be spotted |
| **Node ID** | A Meshtastic radio's address, like `!a1b2c3d4`. Not authenticated on channel messages |
| **PKI DM** | A Meshtastic direct message encrypted to one recipient's public key, available from firmware 2.5 |
| **PSK** | Pre-shared key: a secret both radios are configured with, used to encrypt a channel |
| **Replay attack** | Recording a valid message and sending it again later |
| **Slowloris** | Holding many connections open by sending data very slowly, to exhaust a server |
| **TOFU** | Trust on first use: accepting the first key you see for someone, and trusting it from then on |
| **VLAN** | A separate virtual network on the same switches, used to keep groups of devices apart |

## References

- [Meshtastic: Encryption limitations](https://meshtastic.org/docs/about/overview/encryption/limitations/): channel AES-CTR without authentication; PKI DM caveats
- [Meshtastic: Encryption overview](https://meshtastic.org/docs/overview/encryption)
- [Meshtastic: Bluetooth settings](https://meshtastic.org/docs/configuration/radio/bluetooth/): `FIXED_PIN` 123456 default on screenless devices
- [GHSA-377p-prwp-4hwf / CVE-2025-53627](https://github.com/meshtastic/firmware/security/advisories/GHSA-377p-prwp-4hwf): forged DMs shown as encrypted; fixed in 2.7.15
- [Meshtastic forum: encryption implementation flaw](https://meshtastic.discourse.group/t/meshtastic-encryption-implementation-flaw-corrected-yet/13539)
- [Python docs: `http.server` security considerations](https://docs.python.org/3/library/http.server.html)
- [Home Assistant: webhook trigger](https://www.home-assistant.io/docs/automation/trigger/#webhook-trigger)
