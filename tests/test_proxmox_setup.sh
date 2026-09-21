#!/usr/bin/env bash
# Behaviour checks on scripts/proxmox_setup.sh. Every host path is redirected
# into a temp fixture (PVE_SETUP_PREFIX) and every command that would touch the
# machine (apt-get, sshd, systemctl, journalctl, sysctl, tailscale) is a stub, so
# nothing here changes the machine it runs on. Run from anywhere.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# PVE_SETUP_SCRIPT lets tests/test_setup_sh.py run this suite on a mutant.
SCRIPT="${PVE_SETUP_SCRIPT:-$ROOT/scripts/proxmox_setup.sh}"
fail() {
    echo "FAIL: $*" >&2
    exit 1
}

F="$(mktemp -d)"
trap 'rm -rf "$F"' EXIT
export PVE_SETUP_PREFIX="$F"

# The script's definitions, then stubs that shadow the real commands, then main.
HARNESS="$F/harness.sh"
{
    grep -v '^main "\$@"$' "$SCRIPT"
    cat <<'STUBS'
F="$PVE_SETUP_PREFIX"
require_pve() { :; }
# sshd -t fails on demand. sshd -T resolves like OpenSSH: first value wins,
# drop-ins (lexical order) before the rest of sshd_config, then defaults.
sshd() {
    case "$1" in
        -t) [ -z "${SSHD_T_FAIL:-}" ] ;;
        -T)
            {
                cat "$F"/etc/ssh/sshd_config.d/*.conf 2>/dev/null || true
                cat "$F/etc/ssh/sshd_config"
                printf '%s\n' 'PermitRootLogin prohibit-password' 'PubkeyAuthentication yes' \
                    'PasswordAuthentication yes' 'KbdInteractiveAuthentication yes'
            } | awk 'NF && $1 !~ /^#/ { k = tolower($1); if (!(k in seen)) { seen[k] = 1; print k, $2 } }'
            ;;
    esac
}
systemctl() {
    echo "systemctl $*" >>"$F/calls"
    return "${RELOAD_RC:-0}"
}
journalctl() { cat "$F/journal" 2>/dev/null || true; }
sysctl() {
    case "$1" in
        -n) cat "$F/ip_forward" ;;
        -w) echo 1 >"$F/ip_forward" ;;
    esac
}
tailscale() {
    case "$1" in
        status) [ -f "$F/ts_up" ] ;;
        debug) cat "$F/prefs.json" ;;
        set)
            if [ "${2:-}" = --help ]; then [ -z "${TS_NO_SET:-}" ]; return; fi
            echo "tailscale $*" >>"$F/calls"
            ;;
    esac
}
apt-get() {
    echo "apt-get $*" >>"$F/calls"
    return "${APT_RC:-0}"
}
# HARNESS_CALL=do_repos runs one function directly, bypassing the runner.
if [ -n "${HARNESS_CALL:-}" ]; then
    OPT_YES=1
    "$HARNESS_CALL"
    exit
fi
main "$@"
STUBS
} >"$HARNESS"

run() { bash "$HARNESS" "$@"; }
status_of() { run --status --only "$1" | awk -v s="$1" '$1 == s { print $2 }'; }
calls() { cat "$F/calls" 2>/dev/null || true; }

# Proxmox layout: authorized_keys is a symlink into /etc/pve/priv and already
# holds the node's own key.
mkdir -p "$F/root/.ssh" "$F/etc/pve/priv" "$F/etc/ssh/sshd_config.d" "$F/etc/sysctl.d"
ssh-keygen -q -t ed25519 -N '' -C root@pve1 -f "$F/node"
ssh-keygen -q -t ed25519 -N '' -C operator -f "$F/op"
cp "$F/node.pub" "$F/etc/pve/priv/authorized_keys"
ln -s "$F/etc/pve/priv/authorized_keys" "$F/root/.ssh/authorized_keys"
printf 'PermitRootLogin yes\n' >"$F/etc/ssh/sshd_config"
OP_KEY="$(cat "$F/op.pub")"
OP_FP="$(ssh-keygen -l -f "$F/op.pub" | awk '{print $2}')"

# 1. The node key alone is not an operator key: ssh-keys is todo and
# hardening refuses without writing or reloading anything.
[ "$(status_of ssh-keys)" = todo ] || fail "node key alone counted as an operator key"
if run --yes --only ssh-harden >/dev/null 2>&1; then fail "hardened with only the node key"; fi
[ ! -e "$F/etc/ssh/sshd_config.d/99-local.conf" ] || fail "drop-in written without an operator key"
[ -z "$(calls)" ] || fail "reloaded without an operator key"

# 2. ssh-keys appends the operator key once, through the symlink, keeping the
# node key.
PVE_OPERATOR_KEY="$OP_KEY" run --yes --only ssh-keys >/dev/null
PVE_OPERATOR_KEY="$OP_KEY" run --yes --only ssh-keys >/dev/null
[ -L "$F/root/.ssh/authorized_keys" ] || fail "authorized_keys symlink replaced"
grep -qF "$(cat "$F/node.pub")" "$F/etc/pve/priv/authorized_keys" || fail "node key lost"
[ "$(grep -cF "$OP_KEY" "$F/etc/pve/priv/authorized_keys")" = 1 ] || fail "operator key not added exactly once"
[ "$(status_of ssh-keys)" = "done" ] || fail "ssh-keys not done after adding the operator key"

# 3. No logged key login yet: hardening refuses.
if run --yes --only ssh-harden >/dev/null 2>&1; then fail "hardened before any operator key login"; fi
[ ! -e "$F/etc/ssh/sshd_config.d/99-local.conf" ] || fail "drop-in written before a key login"

# 4. An earlier include that keeps passwords on: refuse, remove our file, stay todo.
echo "Accepted publickey for root from 192.168.1.50 port 51000 ssh2: ED25519 $OP_FP" >"$F/journal"
printf 'PasswordAuthentication yes\n' >"$F/etc/ssh/sshd_config.d/00-early.conf"
if run --yes --only ssh-harden >/dev/null 2>&1; then fail "hardened although an earlier include wins"; fi
[ ! -e "$F/etc/ssh/sshd_config.d/99-local.conf" ] || fail "overridden drop-in left behind"
[ -z "$(calls)" ] || fail "reloaded with passwords still on"
rm "$F/etc/ssh/sshd_config.d/00-early.conf"

# 5. sshd -t fails: the previous drop-in comes back and nothing reloads.
printf '# previous\n' >"$F/etc/ssh/sshd_config.d/99-local.conf"
if SSHD_T_FAIL=1 run --yes --only ssh-harden >/dev/null 2>&1; then fail "hardened with sshd -t failing"; fi
[ "$(cat "$F/etc/ssh/sshd_config.d/99-local.conf")" = "# previous" ] || fail "previous drop-in not restored"
[ -z "$(calls)" ] || fail "reloaded after sshd -t failed"
rm "$F/etc/ssh/sshd_config.d/99-local.conf"

# 6. Reload fails: the drop-in is removed again so the step stays todo.
if RELOAD_RC=1 run --yes --only ssh-harden >/dev/null 2>&1; then fail "reload failure not reported"; fi
[ ! -e "$F/etc/ssh/sshd_config.d/99-local.conf" ] || fail "drop-in kept after a failed reload"
[ "$(status_of ssh-harden)" = todo ] || fail "ssh-harden done after a failed reload"
rm -f "$F/calls"

# 7. The happy path: root by key only, reloaded, and the check agrees.
run --yes --only ssh-harden >/dev/null || fail "hardening failed on the happy path"
calls | grep -qx 'systemctl reload ssh' || fail "ssh not reloaded"
[ "$(status_of ssh-harden)" = "done" ] || fail "ssh-harden not done after hardening"
rm -f "$F/calls"

# 8. Subnet router: an old client without `tailscale set` is refused before
# the sysctl file is touched.
touch "$F/ts_up"
echo 0 >"$F/ip_forward"
printf 'vm.swappiness = 10\nnet.ipv4.ip_forward = 0\n' >"$F/etc/sysctl.d/99-tailscale.conf"
echo '{"AdvertiseRoutes":["10.20.0.0/16","0.0.0.0/0","::/0"]}' >"$F/prefs.json"
if TS_NO_SET=1 run --yes --only subnet-router >/dev/null 2>&1; then fail "subnet router ran without tailscale set"; fi
grep -qx 'net.ipv4.ip_forward = 0' "$F/etc/sysctl.d/99-tailscale.conf" || fail "sysctl file touched by a refused run"

# 9. It adds the LAN to the routes already advertised, forwards IPv4 only and
# keeps the file's other lines.
run --yes --only subnet-router >/dev/null
calls | grep -qx 'tailscale set --advertise-routes=10.20.0.0/16,192.168.1.0/24' ||
    fail "advertised routes not unioned: $(calls)"
grep -qx 'vm.swappiness = 10' "$F/etc/sysctl.d/99-tailscale.conf" || fail "other sysctl lines dropped"
grep -qx 'net.ipv4.ip_forward = 1' "$F/etc/sysctl.d/99-tailscale.conf" || fail "ip_forward not persisted"
if grep -q ipv6 "$F/etc/sysctl.d/99-tailscale.conf"; then fail "IPv6 forwarding touched"; fi

# 10. The check reads live forwarding and AdvertiseRoutes itself.
echo '{"AdvertiseRoutes":["10.20.0.0/16","192.168.1.0/24"]}' >"$F/prefs.json"
[ "$(status_of subnet-router)" = "done" ] || fail "subnet-router not done when set up"
echo 0 >"$F/ip_forward"
[ "$(status_of subnet-router)" = todo ] || fail "subnet-router done with forwarding off"
echo 1 >"$F/ip_forward"
echo '{"AdvertiseRoutes":null,"Note":"192.168.1.0/24"}' >"$F/prefs.json"
[ "$(status_of subnet-router)" = todo ] || fail "route outside AdvertiseRoutes counted"

# --- repos --------------------------------------------------------------------
A="$F/etc/apt/sources.list.d"
mkdir -p "$A"
snapshot() { cat "$F/etc/os-release" "$A"/* 2>/dev/null | cksum; }
fresh_apt() {
    rm -rf "$A" "$F/calls"
    mkdir -p "$A"
    printf 'PRETTY_NAME="Debian GNU/Linux"\nVERSION_CODENAME=%s\n' "$1" >"$F/etc/os-release"
    printf 'Types: deb\nURIs: http://deb.debian.org/debian/\nSuites: %s %s-updates\nComponents: main contrib\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' \
        "$1" "$1" >"$A/debian.sources"
    printf 'deb http://deb.debian.org/debian %s main\n' "$1" >"$F/etc/apt/sources.list"
}

# 11. PVE 9 (deb822) fresh install, Ceph repo present.
fresh_apt trixie
cat >"$A/pve-enterprise.sources" <<'EOF'
# managed by the installer
Types: deb
URIs: https://enterprise.proxmox.com/debian/pve
Suites: trixie
Components: pve-enterprise
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF
cat >"$A/ceph.sources" <<'EOF'
Types: deb
URIs: https://enterprise.proxmox.com/debian/ceph-squid
Suites: trixie
Components: enterprise
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
Enabled: yes
EOF
debian_before="$(cksum <"$A/debian.sources")"
list_before="$(cksum <"$F/etc/apt/sources.list")"
[ "$(status_of repos)" = todo ] || fail "repos done with the enterprise repos enabled"
run --yes --only repos >/dev/null || fail "repos step failed on a fresh PVE 9 layout"
calls | grep -qx 'apt-get update' || fail "apt-get update not run"
[ "$(tail -n1 "$A/pve-enterprise.sources")" = "Enabled: false" ] || fail "pve-enterprise.sources not disabled"
head -n1 "$A/pve-enterprise.sources" | grep -qx '# managed by the installer' || fail "pve-enterprise.sources comment lost"
grep -qx 'Components: pve-enterprise' "$A/pve-enterprise.sources" || fail "pve-enterprise.sources fields lost"
[ "$(grep -i '^enabled:' "$A/ceph.sources")" = "Enabled: false" ] || fail "ceph.sources not disabled exactly once"
[ "$(cksum <"$A/debian.sources")" = "$debian_before" ] || fail "debian.sources touched"
[ "$(cksum <"$F/etc/apt/sources.list")" = "$list_before" ] || fail "sources.list touched"
cat >"$F/want" <<'EOF'
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: trixie
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg

Types: deb
URIs: http://download.proxmox.com/debian/ceph-squid
Suites: trixie
Components: no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF
cmp -s "$F/want" "$A/pve-no-subscription.sources" ||
    fail "pve-no-subscription.sources: $(cat "$A/pve-no-subscription.sources")"
[ "$(status_of repos)" = "done" ] || fail "repos not done after the step"

# 12. Idempotent: the runner skips it, and do_repos itself changes nothing.
before="$(snapshot)"
rm -f "$F/calls"
run --yes --only repos >/dev/null
[ -z "$(calls)" ] || fail "repos reran although done"
HARNESS_CALL=do_repos run >/dev/null || fail "do_repos rerun failed"
[ "$(snapshot)" = "$before" ] || fail "do_repos rerun changed the sources"
[ "$(status_of repos)" = "done" ] || fail "repos not done after a rerun"

# 13. The check reads what apt would use: re-enabled enterprise, a no-subscription
# repo for another suite, or a disabled one, are all todo.
sed -i.bak 's/^Enabled: false$/Enabled: yes/' "$A/pve-enterprise.sources"
[ "$(status_of repos)" = todo ] || fail "repos done with pve-enterprise enabled"
mv "$A/pve-enterprise.sources.bak" "$A/pve-enterprise.sources"
printf 'PRETTY_NAME="Debian"\nVERSION_CODENAME="forky"\n' >"$F/etc/os-release"
[ "$(status_of repos)" = todo ] || fail "repos done with no-subscription for another suite"
printf 'VERSION_CODENAME=trixie\n' >"$F/etc/os-release"
sed -i.bak '5a\
Enabled: no
' "$A/pve-no-subscription.sources"
[ "$(status_of repos)" = todo ] || fail "repos done with no-subscription disabled"
rm -f "$A"/*.bak

# 14. PVE 8 (.list) layout on bookworm, no Ceph repo; apt-get failing fails the step.
fresh_apt bookworm
printf '# keep me\ndeb https://enterprise.proxmox.com/debian/pve bookworm pve-enterprise\n' >"$A/pve-enterprise.list"
[ "$(status_of repos)" = todo ] || fail "repos done with the .list enterprise repo enabled"
if APT_RC=100 run --yes --only repos >/dev/null 2>&1; then fail "apt-get update failure not reported"; fi
rm -f "$A/pve-no-subscription.sources"
# Start over from the uncommented file.
printf '# keep me\ndeb https://enterprise.proxmox.com/debian/pve bookworm pve-enterprise\n' >"$A/pve-enterprise.list"
run --yes --only repos >/dev/null || fail "repos step failed on a PVE 8 layout"
[ "$(cat "$A/pve-enterprise.list")" = "$(printf '# keep me\n# deb https://enterprise.proxmox.com/debian/pve bookworm pve-enterprise')" ] ||
    fail "pve-enterprise.list not commented in place: $(cat "$A/pve-enterprise.list")"
grep -qx 'Suites: bookworm' "$A/pve-no-subscription.sources" || fail "suite not taken from os-release"
if grep -q ceph "$A/pve-no-subscription.sources"; then fail "Ceph repo added without a Ceph enterprise repo"; fi
[ "$(status_of repos)" = "done" ] || fail "repos not done on the PVE 8 layout"
before="$(snapshot)"
HARNESS_CALL=do_repos run >/dev/null
[ "$(snapshot)" = "$before" ] || fail "do_repos rerun changed the .list layout"

# 15. No-subscription already enabled in another file (what the PVE 9 web UI
# writes): done, and no duplicate is added.
fresh_apt trixie
printf 'Types: deb\nURIs: http://download.proxmox.com/debian/pve/\nSuites: trixie\nComponents: pve-no-subscription\nSigned-By: /usr/share/keyrings/proxmox-archive-keyring.gpg\n' >"$A/proxmox.sources"
[ "$(status_of repos)" = "done" ] || fail "existing no-subscription repo not recognised"
HARNESS_CALL=do_repos run >/dev/null
[ ! -e "$A/pve-no-subscription.sources" ] || fail "duplicate no-subscription repo written"

echo "ok: proxmox_setup.sh behaviour"
