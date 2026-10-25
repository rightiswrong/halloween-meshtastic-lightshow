# Home Assistant setup

The translator calls exactly four scripts. Everything else (which light, which speaker,
which clips) is decided inside Home Assistant, so you can change the show without
touching the radios or the services.

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
4. Restart Home Assistant, or reload Scripts, Input booleans and Automations.

The Gledopto must be added to the **Spooky Sound** integration, so that
`spooky_sound.send_cue` can reach its *Sound cue* number.

## 2. What the scripts do

| Script | Effect on the overhead light | Sound |
|---|---|---|
| `script.storm_strike` (`strength` 0–1) | Switches to Lightning Storm and sends one `thunder` cue | One thunder clip, 0.4–2.2 s after the flash |
| `script.storm_storm` | Lightning Storm: a `rumble` glow and six strikes over about 30 s | Thunder after each strike |
| `script.storm_fire` | Candle Flicker with `fire` and `crackle` cues for about 15 s | — |
| `script.storm_heart` | Tell-Tale Heart, with 16 beats that speed up | — |

Before the first summon of a burst, `storm_prepare` snapshots the light. About 5–10 s
after the last one, `storm_settle` restores it. The `input_boolean.storm_light_borrowed`
helper keeps track of whether a restore is pending.

## 3. Show hours

`input_boolean.storm_show_running` gates everything. When it's off, the translator answers
`CLOSED` and guests see "The storm sleeps". The package includes two automations that
switch it on at 18:00 and off at 22:00. Edit the times, or toggle it by hand. To ignore
show hours, remove `show_entity` from `translator.toml`.

## 4. A dedicated user and token for the translator

1. Go to Settings → People → **Add person**. Name it `storm-translator`, allow login,
   and leave *Administrator* off.
2. Log in as that user, then go to Profile → Security → **Long-lived access tokens** → Create.
3. On the translator Pi, paste the token with `sudoedit /etc/storm/ha.token`.

> **Important:** Home Assistant has no per-service permissions for tokens. Any valid
> token can call any service. What limits the mesh to these four scripts is the
> translator's allowlist, not the token. So keep the token only on the translator Pi, readable only
> by the `storm-translator` user (the installer sets this up), and never put it on the
> edge station.
