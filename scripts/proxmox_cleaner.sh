#!/usr/bin/env bash
set -euo pipefail

# Run as root on Proxmox VE 9
if (( EUID != 0 )); then
    echo "Run this script as root."
    exit 1
fi

DIR=/etc/apt/sources.list.d
KEY=/usr/share/keyrings/proxmox-archive-keyring.gpg

# Check the Proxmox repository signing key
if [[ ! -f "$KEY" ]]; then
    echo "Missing Proxmox archive keyring: $KEY"
    exit 1
fi

# Back up repository configuration
BACKUP="/root/apt-repos-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP"
cp -a /etc/apt/sources.list "$BACKUP/" 2>/dev/null || true
cp -a "$DIR" "$BACKUP/"

echo "Backup saved: $BACKUP"

# Disable a single-entry enterprise .sources file
disable_enterprise_source() {
    local file="$1"
    [[ -f "$file" ]] || return 0

    if [[ $(grep -c '^Types:' "$file" || true) -ne 1 ]]; then
        echo "Multiple entries in $file; review manually."
        exit 1
    fi

    if grep -q '^Enabled:' "$file"; then
        sed -i 's/^Enabled:.*/Enabled: no/' "$file"
    else
        sed -i '/^Types:/a Enabled: no' "$file"
    fi
}

echo "Disabling PVE enterprise repositories..."
disable_enterprise_source "$DIR/pve-enterprise.sources"

if [[ -f "$DIR/pve-enterprise.list" ]]; then
    sed -i -E \
      '/^[[:space:]]*deb .*enterprise.proxmox.com\/debian\/pve/s/^/# /' \
      "$DIR/pve-enterprise.list"
fi

echo "Handling Ceph enterprise repositories..."

if [[ -f "$DIR/ceph.sources" ]] &&
   grep -q 'enterprise.proxmox.com/debian/ceph' "$DIR/ceph.sources"; then
    disable_enterprise_source "$DIR/ceph.sources"
fi

if [[ -f "$DIR/ceph.list" ]]; then
    sed -i -E \
      '/^[[:space:]]*deb .*enterprise.proxmox.com\/debian\/ceph/s/^/# /' \
      "$DIR/ceph.list"
fi

echo "Checking for an existing no-subscription repository..."

if grep -REq \
  '^(deb .*download\.proxmox\.com/debian/pve.*pve-no-subscription|Components:[[:space:]]*pve-no-subscription)' \
  /etc/apt/sources.list "$DIR" 2>/dev/null; then
    echo "Existing no-subscription entry detected."
    echo "Review it for duplicates or disabled entries."
else
    cat > "$DIR/proxmox.sources" <<EOF
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: trixie
Components: pve-no-subscription
Signed-By: $KEY
EOF
    echo "Created Proxmox no-subscription repository."
fi

echo "Refreshing APT..."
apt update

echo "Repository setup finished."
echo "Review pending upgrades with: apt -s full-upgrade"
