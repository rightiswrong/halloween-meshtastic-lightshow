# Rotating keys and secrets

Rotate after any loss or theft of a radio, the edge box or the translator Pi, after
someone you no longer trust has seen a secret, and once a year anyway. Each item below
is independent, so rotate only what was exposed.

| Secret | Where it lives | Exposed if someone gets... |
|---|---|---|
| Private channel PSK | Both T-Beams' flash | either radio, or the edge CM4 (it can read the edge radio's config) |
| Link secret | `/etc/storm/link.secret` on the edge CM4 **and** the translator Pi | the edge CM4 or the translator Pi |
| Webhook id | `/etc/storm/webhook.id` on the translator Pi, and HA `secrets.yaml` | the translator Pi or the HA config |
| Porch Wi-Fi password (TCP mode) | Porch T-Beam's flash | the porch radio |

## Private channel PSK

```bash
openssl rand -base64 32                                     # new key
meshtastic --ch-index 1 --ch-set psk "base64:<new key>"     # on BOTH radios
```

Until both radios have the new key, summons fail with "the spirits didn't answer".

## Link secret

```bash
# On the edge CM4:
sudo sh -c 'openssl rand -base64 32 > /etc/storm/link.secret'
sudo cat /etc/storm/link.secret | ssh <translator> 'sudo tee /etc/storm/link.secret >/dev/null'
sudo systemctl restart storm-bbs
ssh <translator> sudo systemctl restart storm-translator
```

The sequence counters don't need resetting: they keep counting up under the new secret.

## Webhook id

```bash
# On the translator Pi:
sudo sh -c 'openssl rand -hex 24 > /etc/storm/webhook.id' && sudo cat /etc/storm/webhook.id
```

Put the new value in Home Assistant's `/config/secrets.yaml` as `storm_webhook_id`,
restart Home Assistant, then restart `storm-translator`.

## Porch Wi-Fi password

Change it on the router or access point, then set it on the porch radio
(`meshtastic --set network.wifi_psk "<new>"`). Every other device on that IoT network
needs the new password too, which is one reason to keep that network small.

## After the season

- `meshtastic --factory-reset` on both radios, or rotate the PSK before reusing them.
- Revoke any Home Assistant token you used in token mode (Profile → Security).
- `sudo rm /etc/storm/link.secret /etc/storm/webhook.id` if the boxes are being repurposed.
