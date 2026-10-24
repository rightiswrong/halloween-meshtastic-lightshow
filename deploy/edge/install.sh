#!/usr/bin/env bash
# Edge station installer: Raspberry Pi OS Lite (Bookworm or newer) on the CM4.
# Run from a checkout of this repo, while the CM4 is on Ethernet (it needs apt + pip once):
#   sudo ./deploy/edge/install.sh
# Re-running is safe. It never edits /etc/storm/edge.toml once it exists.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
HERE="$REPO/deploy/edge"

echo "==> packages"
apt-get update -q
apt-get install -y -q hostapd dnsmasq nftables python3-venv rfkill

echo "==> service user"
id storm >/dev/null 2>&1 || useradd --system --home-dir /var/lib/storm-bbs --shell /usr/sbin/nologin storm
usermod -aG dialout storm

echo "==> application in /opt/storm-bbs"
python3 -m venv /opt/storm-bbs
/opt/storm-bbs/bin/pip install -q --upgrade pip
/opt/storm-bbs/bin/pip install -q "$REPO"

echo "==> configuration"
install -d -m 755 /etc/storm
if [[ ! -f /etc/storm/edge.toml ]]; then
  install -m 644 "$REPO/config/edge.example.toml" /etc/storm/edge.toml
  echo "    created /etc/storm/edge.toml: set porch_node_id and the serial port"
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

echo "==> checking for home Wi-Fi credentials (this box must not have any)"
if command -v nmcli >/dev/null && nmcli -t -f TYPE connection show | grep -q '^802-11-wireless$'; then
  echo "    WARNING: NetworkManager has saved Wi-Fi connections:"
  nmcli -t -f NAME,TYPE connection show | grep ':802-11-wireless$' | sed 's/^/      /'
  echo "    Delete them (sudo nmcli connection delete <name>) before deploying at the edge."
fi
if [[ -s /etc/wpa_supplicant/wpa_supplicant.conf ]] && grep -q 'psk=' /etc/wpa_supplicant/wpa_supplicant.conf; then
  echo "    WARNING: /etc/wpa_supplicant/wpa_supplicant.conf holds a Wi-Fi password. Remove it."
fi

cat <<'DONE'

Done. Next:
  1. Edit /etc/storm/edge.toml (porch_node_id; serial port, ideally /dev/serial/by-id/...).
  2. sudo reboot   (brings up the hotspot, firewall and service in order)
  3. Check:  systemctl status storm-bbs   and   journalctl -u storm-bbs -f
  4. Bench test without radios first:  /opt/storm-bbs/bin/storm-bbs --demo --listen 0.0.0.0
DONE
