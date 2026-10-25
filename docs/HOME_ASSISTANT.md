# Home Assistant setup

The translator reaches Home Assistant through **one webhook** that can only start four
scripts. Everything else (which light, which speaker, which clips) is decided inside
Home Assistant, so you can change the show without touching the radios or the services.

## 1. Install the package

1. In `configuration.yaml`:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
2. Copy [`homeassistant/packages/storm.yaml`](../homeassistant/packages/storm.yaml) to
   `/config/packages/storm.yaml`.
3. Replace the placeholders marked `CHANGE ME`:
   - `light.spooky_overhead`: the Gledopto light running Spooky Overhead.
   - `media_player.porch_speaker`: the speaker for the thunder.
   - Sound files: put `thunder_1.mp3` to `thunder_3.mp3` in `/media/storm/`.
4. Add the webhook id to `/config/secrets.yaml`. The translator installer generated it
   and printed it; it's also in `/etc/storm/webhook.id` on the translator Pi:
   ```yaml
   storm_webhook_id: "3f9c...the 48-character id..."
   ```
5. Restart Home Assistant.

The Gledopto must be added to the **Spooky Sound** integration, so that
`spooky_sound.send_cue` can reach its *Sound cue* number.

## 2. How the translator gets in: the webhook

The `storm_webhook` automation is the only door:

- **No token.** The translator Pi holds no Home Assistant credentials. A stolen webhook
  id can do exactly what the automation allows, and no more (SECURITY-AUDIT.md F-02).
- **Local only, POST only** (`local_only: true`): requests from the internet through
  Nabu Casa or a reverse proxy are refused.
- **Strict checks.** It accepts only the verbs `STRIKE`, `STORM`, `FIRE` and `HEART`,
  clamps `strength` to 0–1, and does nothing while `input_boolean.storm_show_running`
  is off.

Home Assistant answers every webhook call with HTTP 200, even one it ignores. So to
see what happened, open the automation's **Traces**.

## 3. What the scripts do

| Script | Effect on the overhead light | Sound |
|---|---|---|
| `script.storm_strike` (`strength` 0–1) | Switches to Lightning Storm and sends one `thunder` cue | One thunder clip, 0.4–2.2 s after the flash |
| `script.storm_storm` | Lightning Storm: a `rumble` glow and six strikes over about 30 s | Thunder after each strike |
| `script.storm_fire` | Candle Flicker with `fire` and `crackle` cues for about 15 s | — |
| `script.storm_heart` | Tell-Tale Heart, with 16 beats that speed up | — |

Before the first summon of a burst, `storm_prepare` snapshots the light. About 5–10 s
after the last one, `storm_settle` restores it. The `input_boolean.storm_light_borrowed`
helper keeps track of whether a restore is pending.

## 4. Show hours

There are two switches, and both must say yes:

- **On the translator:** `[show] hours = "18:00-22:00"` in `translator.toml`. Outside these
  hours, guests are told "The storm sleeps", and nothing is sent to Home Assistant.
- **In Home Assistant:** `input_boolean.storm_show_running`. The package's automations
  switch it on at 18:00 and off at 22:00. Turn it off by hand to stop the show at once.
  (The translator can't see this switch in webhook mode, so guests still get a
  "summoned" reply while it's off.)

## 5. Legacy token mode

`mode = "token"` in `translator.toml` calls `script.turn_on` with a long-lived access
token, and can read `input_boolean.storm_show_running` to answer guests accurately.

> **Avoid it if you can.** Home Assistant tokens can't be restricted: any valid token,
> even a non-admin user's, can call **any** service, including unlocking doors or
> disarming an alarm. If you must use it, create a dedicated non-admin user, keep the
> token only in `/etc/storm/ha.token` on the translator Pi (mode 640), use HTTPS to Home
> Assistant (F-12), and revoke the token after the season.
