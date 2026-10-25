# shellcheck shell=bash
# Shared helpers for the installers. Sourced, not run.

# Install the app into a venv WITHOUT running pip as root (SECURITY-AUDIT.md F-13):
# the venv is built by the service user, then handed to root so the service can't
# modify its own code.  usage: install_app <venv> <service-user> <repo>
install_app() {
  local venv="$1" user="$2" repo="$3" src
  src="$(mktemp -d)"
  # The repo may live in a private home directory; give the build user a readable copy.
  tar -C "$repo" --exclude=.git --exclude='__pycache__' -cf - . | tar -C "$src" -xf -
  chmod -R a+rX "$src"
  rm -rf "$venv"
  install -d -o "$user" -g "$user" "$venv"
  runuser -u "$user" -- python3 -m venv "$venv"
  runuser -u "$user" -- env HOME="$src" PIP_DISABLE_PIP_VERSION_CHECK=1 \
    "$venv/bin/pip" install --no-cache-dir -q "$src"
  chown -R root:root "$venv"
  rm -rf "$src"
}

# Create a root-owned secret file readable by the service group, if it doesn't exist.
# usage: make_secret <path> <group> <generator command...>   (empty file if no generator)
make_secret() {
  local path="$1" group="$2"; shift 2
  [[ -s "$path" ]] && return 0
  install -m 640 -o root -g "$group" /dev/null "$path"
  if [[ $# -gt 0 ]]; then "$@" > "$path"; fi
}

# Keys-only SSH (F-08). Refuses if the invoking user has no authorized_keys, so you
# can't lock yourself out.
harden_ssh() {
  local here="$1" who="${SUDO_USER:-}" keys
  keys="$(getent passwd "$who" | cut -d: -f6)/.ssh/authorized_keys"
  if [[ -z "$who" || ! -s "$keys" ]]; then
    echo "    SKIPPED --harden-ssh: no SSH key in $keys. Add one first (ssh-copy-id), then re-run."
    return 0
  fi
  install -d -m 755 /etc/ssh/sshd_config.d
  install -m 644 "$here/../common/90-storm-ssh.conf" /etc/ssh/sshd_config.d/90-storm-ssh.conf
  sshd -t && systemctl reload ssh
  echo "    SSH now accepts keys only."
}
