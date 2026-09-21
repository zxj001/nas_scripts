#!/usr/bin/env bash
# Behaviour checks on scripts/proxmox_setup.sh. Every host path is redirected
# into a temp fixture (PVE_SETUP_PREFIX) and every command that would touch the
# machine (sshd, systemctl, journalctl, sysctl, tailscale) is a stub, so
# nothing here changes the machine it runs on. Run from anywhere.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/proxmox_setup.sh"
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

echo "ok: proxmox_setup.sh behaviour"
