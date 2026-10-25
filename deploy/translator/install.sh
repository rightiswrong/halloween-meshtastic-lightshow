#!/usr/bin/env bash
# Translator installer: Raspberry Pi on the latest Raspberry Pi OS (Lite is enough),
# on the same subnet as Home Assistant. Any Debian-based Linux works the same way.
#   sudo ./deploy/translator/install.sh [--usb] [--harden-ssh]
#     --usb         the porch T-Beam is plugged into this Pi (instead of Wi-Fi/TCP)
#     --harden-ssh  SSH keys only (needs your key installed first)
# Re-running is safe. It never overwrites the config or the secrets once they exist.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
HERE="$REPO/deploy/translator"
# shellcheck source=../common/lib.sh
source "$REPO/deploy/common/lib.sh"
USB=0
HARDEN_SSH=0
for arg in "$@"; do
  case "$arg" in
    --usb) USB=1 ;;
    --harden-ssh) HARDEN_SSH=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

echo "==> packages"
apt-get update -q
apt-get install -y -q python3-venv openssl

echo "==> service user"
id storm-translator >/dev/null 2>&1 || \
  useradd --system --home-dir /var/lib/storm-translator --shell /usr/sbin/nologin storm-translator

echo "==> application in /opt/storm-translator (built as 'storm-translator', owned by root)"
install_app /opt/storm-translator storm-translator "$REPO"

echo "==> configuration"
install -d -m 750 -o root -g storm-translator /etc/storm
if [[ ! -f /etc/storm/translator.toml ]]; then
  install -m 640 -o root -g storm-translator "$REPO/config/translator.example.toml" /etc/storm/translator.toml
  echo "    created /etc/storm/translator.toml: set host (or port), edge_node_ids and the HA url"
fi
if [[ ! -s /etc/storm/link.secret ]]; then
  make_secret /etc/storm/link.secret storm-translator
  echo "    created an empty /etc/storm/link.secret: copy the edge's link secret into it"
fi
if [[ ! -s /etc/storm/webhook.id ]]; then
  make_secret /etc/storm/webhook.id storm-translator openssl rand -hex 24
  echo "    generated /etc/storm/webhook.id. Put the same value in Home Assistant's"
  echo "    /config/secrets.yaml as:  storm_webhook_id: \"$(cat /etc/storm/webhook.id)\""
fi

install -m 644 "$HERE/storm-translator.service" /etc/systemd/system/storm-translator.service
if [[ $USB -eq 1 ]]; then
  usermod -aG dialout storm-translator
  install -d -m 755 /etc/systemd/system/storm-translator.service.d
  install -m 644 "$HERE/usb-radio.conf" /etc/systemd/system/storm-translator.service.d/usb-radio.conf
  echo "    USB radio: set [radio] port (not host) in /etc/storm/translator.toml"
  echo "    Consider USBGuard so only the radio is accepted on USB (docs/SECURITY.md)."
fi
systemctl daemon-reload
systemctl enable storm-translator.service

if [[ $HARDEN_SSH -eq 1 ]]; then
  echo "==> SSH"
  harden_ssh "$HERE"
fi

cat <<'DONE'

Done. Next:
  1. Edit /etc/storm/translator.toml; paste the edge's link secret: sudoedit /etc/storm/link.secret
  2. Add storm_webhook_id to Home Assistant's secrets.yaml (above) and install the package.
  3. sudo -u storm-translator /opt/storm-translator/bin/storm-translator --check
  4. sudo -u storm-translator /opt/storm-translator/bin/storm-translator --test STRIKE   (test T0)
  5. sudo systemctl start storm-translator && journalctl -u storm-translator -f
DONE
