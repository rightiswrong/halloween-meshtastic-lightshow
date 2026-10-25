#!/usr/bin/env bash
# Translator installer: Raspberry Pi on the latest Raspberry Pi OS (Lite is enough),
# on the same subnet as Home Assistant. Any Debian-based Linux works the same way.
#   sudo ./deploy/translator/install.sh          # porch T-Beam over Wi-Fi/TCP (default)
#   sudo ./deploy/translator/install.sh --usb    # porch T-Beam plugged into this Pi
# Re-running is safe. It never edits /etc/storm/translator.toml or the token once they exist.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
HERE="$REPO/deploy/translator"
USB=0
[[ "${1:-}" == "--usb" ]] && USB=1

echo "==> packages"
apt-get update -q
apt-get install -y -q python3-venv

echo "==> service user"
id storm-translator >/dev/null 2>&1 || \
  useradd --system --home-dir /var/lib/storm-translator --shell /usr/sbin/nologin storm-translator

echo "==> application in /opt/storm-translator"
python3 -m venv /opt/storm-translator
/opt/storm-translator/bin/pip install -q --upgrade pip
/opt/storm-translator/bin/pip install -q "$REPO"

echo "==> configuration"
install -d -m 750 -o root -g storm-translator /etc/storm
if [[ ! -f /etc/storm/translator.toml ]]; then
  install -m 640 -o root -g storm-translator "$REPO/config/translator.example.toml" /etc/storm/translator.toml
  echo "    created /etc/storm/translator.toml: set host, edge_node_ids and the HA url"
fi
if [[ ! -f /etc/storm/ha.token ]]; then
  install -m 640 -o root -g storm-translator /dev/null /etc/storm/ha.token
  echo "    created an empty /etc/storm/ha.token: paste the HA long-lived token into it"
fi

install -m 644 "$HERE/storm-translator.service" /etc/systemd/system/storm-translator.service
if [[ $USB -eq 1 ]]; then
  usermod -aG dialout storm-translator
  install -d -m 755 /etc/systemd/system/storm-translator.service.d
  install -m 644 "$HERE/usb-radio.conf" /etc/systemd/system/storm-translator.service.d/usb-radio.conf
  echo "    USB radio: set [radio] port (not host) in /etc/storm/translator.toml"
fi
systemctl daemon-reload
systemctl enable storm-translator.service

cat <<'DONE'

Done. Next:
  1. Edit /etc/storm/translator.toml and fill /etc/storm/ha.token (sudoedit /etc/storm/ha.token).
  2. sudo -u storm-translator /opt/storm-translator/bin/storm-translator --check
  3. sudo -u storm-translator /opt/storm-translator/bin/storm-translator --test STRIKE   (test T0)
  4. sudo systemctl start storm-translator && journalctl -u storm-translator -f
DONE
