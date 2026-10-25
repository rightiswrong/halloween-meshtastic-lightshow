#!/usr/bin/env bash
# Edge station installer: latest Raspberry Pi OS Lite on the CM4.
# Run from a checkout of this repo, while the CM4 is on Ethernet (it needs apt + pip once):
#   sudo ./deploy/edge/install.sh [--harden-ssh]
# Re-running is safe. It never overwrites /etc/storm/edge.toml or the link secret.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
HERE="$REPO/deploy/edge"
# shellcheck source=../common/lib.sh
source "$REPO/deploy/common/lib.sh"
HARDEN_SSH=0
for arg in "$@"; do
  case "$arg" in
    --harden-ssh) HARDEN_SSH=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

echo "==> packages"
apt-get update -q
apt-get install -y -q hostapd dnsmasq nftables python3-venv rfkill openssl

echo "==> service user"
id storm >/dev/null 2>&1 || useradd --system --home-dir /var/lib/storm-bbs --shell /usr/sbin/nologin storm
usermod -aG dialout storm

echo "==> application in /opt/storm-bbs (built as 'storm', owned by root)"
install_app /opt/storm-bbs storm "$REPO"

echo "==> configuration"
install -d -m 750 -o root -g storm /etc/storm
if [[ ! -f /etc/storm/edge.toml ]]; then
  install -m 640 -o root -g storm "$REPO/config/edge.example.toml" /etc/storm/edge.toml
  echo "    created /etc/storm/edge.toml: set porch_node_id and the serial port"
fi
if [[ ! -s /etc/storm/link.secret ]]; then
  make_secret /etc/storm/link.secret storm openssl rand -base64 32
  echo "    generated /etc/storm/link.secret. Copy it to the translator Pi (same path):"
  echo "      sudo cat /etc/storm/link.secret | ssh <translator> 'sudo tee /etc/storm/link.secret >/dev/null'"
fi
install -m 644 "$HERE/storm-firewall.nft" /etc/storm/storm-firewall.nft
install -m 644 "$HERE/hostapd.conf" /etc/hostapd/hostapd.conf
install -m 644 "$HERE/dnsmasq-storm.conf" /etc/dnsmasq.d/storm.conf
install -m 644 "$HERE/90-storm-no-forward.conf" /etc/sysctl.d/90-storm-no-forward.conf
install -d -m 755 /etc/NetworkManager/conf.d
install -m 644 "$HERE/90-storm-unmanaged.conf" /etc/NetworkManager/conf.d/90-storm-unmanaged.conf
sysctl -q --system
# Debian's dnsmasq reads /etc/dnsmasq.d/ by default; storm.conf binds to wlan0 only,
# so nothing is served on Ethernet.

echo "==> systemd units"
for unit in storm-ap.service storm-firewall.service storm-bbs.service; do
  install -m 644 "$HERE/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl unmask hostapd
systemctl reload NetworkManager 2>/dev/null || true
systemctl enable storm-firewall.service storm-ap.service hostapd dnsmasq storm-bbs.service

if [[ $HARDEN_SSH -eq 1 ]]; then
  echo "==> SSH"
  harden_ssh "$HERE"
fi

echo "==> checks"
if command -v nmcli >/dev/null && nmcli -t -f TYPE connection show | grep -q '^802-11-wireless$'; then
  echo "    WARNING: NetworkManager has saved Wi-Fi connections (this box must have none):"
  nmcli -t -f NAME,TYPE connection show | grep ':802-11-wireless$' | sed 's/^/      /'
  echo "    Delete them: sudo nmcli connection delete <name>"
fi
if [[ -s /etc/wpa_supplicant/wpa_supplicant.conf ]] && grep -q 'psk=' /etc/wpa_supplicant/wpa_supplicant.conf; then
  echo "    WARNING: /etc/wpa_supplicant/wpa_supplicant.conf holds a Wi-Fi password. Remove it."
fi
if [[ $HARDEN_SSH -eq 0 ]] && sshd -T 2>/dev/null | grep -qi '^passwordauthentication yes'; then
  echo "    NOTE: SSH accepts passwords. Add a key (ssh-copy-id) and re-run with --harden-ssh."
fi

cat <<'DONE'

Done. Next:
  1. Edit /etc/storm/edge.toml (porch_node_id; serial port, ideally /dev/serial/by-id/...).
  2. Copy /etc/storm/link.secret to the translator Pi (see above).
  3. sudo reboot   (brings up the hotspot, firewall and service in order)
  4. Check:  journalctl -u storm-bbs -f   (look for "RADIO CHECK" warnings)
  5. Bench test without radios first:  /opt/storm-bbs/bin/storm-bbs --demo   (http://127.0.0.1:8080/)
DONE
