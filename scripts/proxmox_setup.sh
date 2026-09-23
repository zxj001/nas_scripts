#!/usr/bin/env bash
# proxmox_setup - apt repositories, SSH keys, SSH hardening, Tailscale and the
# ShellFish widget on a Proxmox VE host,
# the way docs/pve-host.md describes. The host-side analogue of setup.sh.
#
#   curl -fsSL https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/proxmox_setup.sh | bash
#
# Rerunning that line is the update: nothing is cloned onto the hypervisor.
# Everything below is a definition; the last line calls main, so a half
# downloaded copy does nothing.
set -Eeuo pipefail

LAN_ROUTE="192.168.1.0/24"

# Test hook: tests/test_proxmox_setup.sh points every path below into a
# fixture. Unset on a real host.
P="${PVE_SETUP_PREFIX:-}"
AUTH_KEYS="$P/root/.ssh/authorized_keys"
# Fingerprints of the keys the operator gave this script. Proxmox adds its own
# node (and cluster) keys to authorized_keys, so a nonempty file proves nothing
# about the operator's access.
OPERATOR_FPS="$P/root/.ssh/operator-keys"
SSHD_DROPIN="$P/etc/ssh/sshd_config.d/99-local.conf"
SYSCTL_FILE="$P/etc/sysctl.d/99-tailscale.conf"
APT_DIR="$P/etc/apt/sources.list.d"
OS_RELEASE="$P/etc/os-release"
SHELLFISHRC="$P/root/.shellfishrc"
WIDGET_BIN="$P/usr/local/bin/shellfish_widget.sh"
WIDGET_URL="https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/shellfish_widget.sh"

# Ordered step registry, same contract as setup.sh. Every name here needs
# three functions, with "-" replaced by "_":
#
#   check_<name>    0 = done, 1 = todo, 2 = not applicable, 3 = manual setup
#   do_<name>       make it so
#   default_<name>  echo yes|no - the prompt default
#
# tests/test_setup.sh fails if a registered step is missing one.
#
# Nothing else belongs here: no upgrade, guest agent, sleep or dev tools on the
# hypervisor. Proxmox manages its own packages. shellfish only adds the small
# packages its widget needs, one script in /usr/local/bin and a cron line.
STEPS=(repos ssh-keys ssh-harden tailscale subnet-router shellfish)

usage() {
    cat <<'EOF'
Usage: proxmox_setup.sh [--status] [--yes] [--only a,b] [--help]

  --status     run the checks, print the table and exit
  --yes        run every not-done step without prompting
  --only a,b   only these steps, comma separated
  --help       this text

shellfish is manual until Shell Integration is installed from the ShellFish
app while connected as root.

PVE_OPERATOR_KEY="ssh-ed25519 AAAA..." supplies your public key to ssh-keys
(needed with --yes). ssh-harden refuses until sshd has logged a root login
with that key, so ssh in with it once before hardening.
EOF
}

log() { printf '==> %s\n' "$*"; }

have() { command -v "$1" >/dev/null 2>&1; }

# check/do/default function name for a step ("ssh-keys" -> "check_ssh_keys")
fname() { printf '%s_%s' "$1" "${2//-/_}"; }

# Proxmox VE only, as root. Anything else gets setup.sh instead.
require_pve() {
    if [ ! -d /etc/pve ] || ! have pveversion || ! pveversion >/dev/null 2>&1; then
        echo "not a Proxmox VE host (no /etc/pve or pveversion) - this script is for the hypervisor only; use scripts/setup.sh elsewhere" >&2
        exit 1
    fi
    if [ "$(id -u)" != 0 ]; then
        echo "run as root on the Proxmox host" >&2
        exit 1
    fi
}

# --- steps ------------------------------------------------------------------

# Debian codename of the running system (trixie on PVE 9, bookworm on PVE 8).
codename() { sed -n 's/^VERSION_CODENAME=//p' "$OS_RELEASE" | tr -d '"'; }

repo_keyring() {
    local key
    for key in /usr/share/keyrings/proxmox-archive-keyring.gpg /etc/apt/trusted.gpg.d/proxmox-release-bookworm.gpg; do
        if [ "$key" = /etc/apt/trusted.gpg.d/proxmox-release-bookworm.gpg ] && [ "$(codename)" != bookworm ]; then
            continue
        fi
        if [ -f "$P$key" ] && [ -s "$P$key" ] && runuser -u _apt -- test -r "$P$key"; then
            printf '%s\n' "$key"
            return 0
        fi
    done
    echo "no eligible Proxmox keyring is nonempty and readable by _apt" >&2
    return 1
}

source_file() {
    local mode="$1" file="$2"
    [ -f "$file" ] || return 0
    awk -v mode="$mode" -v deb822="${file##*.}" -v key="${3:-}" -v suite="$(codename)" \
        -v managed_file="$([ "$file" = "$APT_DIR/pve-no-subscription.sources" ] && echo yes)" '
        function enterprise(uris,    items, count, i) {
            count = split(uris, items, " ")
            for (i = 1; i <= count; i++)
                if (items[i] ~ /^https?:\/\/enterprise\.proxmox\.com([\/:]|$)/) return 1
            return 0
        }
        function field() {
            if (k == "types") types = v
            else if (k == "uris") u = v
            else if (k == "suites") s = v
            else if (k == "components") c = v
            else if (k == "signed-by") signed = v
            else if (k == "enabled") en = (tolower(v) !~ /^(no|false|off|0|disable)/)
        }
        function flush(    i, skip, ent, managed, repair, wrote, suites, current, other) {
            field()
            ent = enterprise(u)
            managed = (managed_file == "yes" &&
                ((u ~ /^http:\/\/download\.proxmox\.com\/debian\/pve\/?$/ && c == "pve-no-subscription") ||
                 (u ~ /^http:\/\/download\.proxmox\.com\/debian\/ceph-[a-z]+\/?$/ && c == "no-subscription")))
            split(s, suites, " "); current = other = 0
            for (i in suites) {
                if (suites[i] == suite) current = 1
                else other = 1
            }
            repair = (mode == "keyring" && en && managed && current && !other && signed != key)
            if (mode == "read") {
                if (u != "") print u "|" s "|" c "|" en "|" ent "|" signed "|" managed "|" types "|" FILENAME
            } else {
                skip = 0
                for (i = 1; i <= n; i++) {
                    if (buf[i] ~ /^[[:space:]]*#/) { print buf[i]; continue }
                    if (buf[i] !~ /^[[:space:]]/) {
                        skip = (mode == "disable" && ent && en && tolower(buf[i]) ~ /^enabled[[:space:]]*:/)
                        if (repair && tolower(buf[i]) ~ /^signed-by[[:space:]]*:/) {
                            skip = 1
                            if (!wrote++) print "Signed-By: " key
                        }
                    }
                    if (!skip) print buf[i]
                }
                if (mode == "disable" && ent && en) print "Enabled: false"
                if (repair && !wrote) print "Signed-By: " key
            }
            u = s = c = k = v = signed = types = ""; en = 1; n = 0
        }
        BEGIN { en = 1 }
        deb822 != "sources" {
            raw = $0; en = 1
            if (sub(/^[[:space:]]*#[[:space:]]*/, "")) en = 0
            sub(/#.*/, "")
            if ($0 ~ /^[[:space:]]*deb(-src)?[[:space:]]/) {
                signed = ""
                if (match($0, /\[[^]]*\]/)) {
                    options = substr($0, RSTART + 1, RLENGTH - 2)
                    count = split(options, opts, " ")
                    for (i = 1; i <= count; i++)
                        if (opts[i] ~ /^signed-by=/) {
                            signed = opts[i]; sub(/^signed-by=/, "", signed)
                            gsub(/,/, " ", signed)
                        }
                }
                sub(/\[[^]]*\][[:space:]]*/, "")
                c = ""; for (i = 4; i <= NF; i++) c = c (c == "" ? "" : " ") $i
                ent = enterprise($2)
                if (mode == "read") print $2 "|" $3 "|" c "|" en "|" ent "|" signed "||" $1 "|" FILENAME
                else if (en && ent) raw = "# " raw
            }
            if (mode != "read") print raw
            next
        }
        /^[[:space:]]*$/ { flush(); if (mode != "read") print; next }
        { buf[++n] = $0 }
        /^[[:space:]]*#/ { next }
        /^[[:space:]]/ {
            sub(/^[[:space:]]+/, "")
            v = v (v == "" ? "" : " ") $0
            next
        }
        {
            field()
            k = tolower($0); sub(/[[:space:]]*:.*/, "", k)
            v = $0; sub(/^[^:]*:[[:space:]]*/, "", v)
        }
        END { if (deb822 == "sources") flush() }' "$file"
}

all_sources() {
    local f
    for f in "$APT_DIR"/*.sources "$APT_DIR"/*.list "$P/etc/apt/sources.list"; do
        source_file read "$f"
    done
}

enabled_sources() { all_sources | awk -F'|' '$4'; }

has_enterprise() { awk -F'|' '$4 && $5 { found = 1 } END { exit !found }'; }

ceph_releases() {
    all_sources | awk -F'|' -v suite="$(codename)" '
        { n = split($1, uris, " ")
          split($2, suites, " "); current = 0
          for (j in suites) if (suites[j] == suite) current = 1
          for (i = 1; i <= n; i++) {
              if (uris[i] ~ /^https?:\/\/enterprise\.proxmox\.com\/debian\/ceph-[a-z]+\/?$/) {
                  sub(/.*ceph-/, "", uris[i]); sub(/\/$/, "", uris[i])
                  if ($4) active[uris[i]] = 1
                  else disabled[uris[i]] = 1
              }
              if ($4 && current && $8 ~ /(^|[[:space:]])deb([[:space:]]|$)/ && uris[i] ~ /^http:\/\/download\.proxmox\.com\/debian\/ceph-[a-z]+\/?$/ &&
                  $3 ~ /(^|[[:space:]])no-subscription([[:space:]]|$)/) replacement = 1
          } }
        END {
            for (release in active) { print release; found = 1 }
            if (!found && !replacement) for (release in disabled) print release
        }' | sort
}

# Is an enabled source for $1 (URI) with suite $2 and component $3 there?
has_source() {
    enabled_sources | awk -F'|' -v u="$1" -v s="$2" -v c="$3" '
        { n = split($1, us, " "); split($2, ss, " "); split($3, cs, " ")
          hu = hs = hc = 0
          for (i = 1; i <= n; i++) { x = us[i]; sub(/\/$/, "", x); if (x == u) hu = 1 }
          for (i in ss) if (ss[i] == s) hs = 1
          for (i in cs) if (cs[i] == c) hc = 1
          if (hu && hs && hc && $8 ~ /(^|[[:space:]])deb([[:space:]]|$)/) found = 1 }
        END { exit !found }'
}

signing_compatible() {
    all_sources | awk -F'|' -v key="$1" -v suite="$(codename)" '
        $4 && $8 ~ /(^|[[:space:]])deb(-src)?([[:space:]]|$)/ {
            split($2, suites, " "); current = other = 0
            for (j in suites) {
                if (suites[j] == suite) current = 1
                else other = 1
            }
            if (!current) next
            if ($7 && other && $6 != key) {
                print "cannot repair Signed-By in mixed-suite managed stanza in " $9 > "/dev/stderr"
                bad = 1
                next
            }
            count = split($6, keys, " ")
            if (count == 1 && keys[1] == key) next
            if ($7 && count == 1 &&
                (keys[1] == "/usr/share/keyrings/proxmox-archive-keyring.gpg" ||
                 (suite == "bookworm" && keys[1] == "/etc/apt/trusted.gpg.d/proxmox-release-bookworm.gpg"))) next
            count = split($1, uris, " ")
            for (i = 1; i <= count; i++)
                if (uris[i] ~ /^http:\/\/download\.proxmox\.com\/debian\/(pve|ceph-[a-z]+)\/?$/) {
                    print "conflicting Signed-By for " uris[i] " " suite " in " $9 "; expected " key > "/dev/stderr"
                    bad = 1
                }
        }
        END { exit bad }'
}

managed_keyring_matches() {
    source_file read "$APT_DIR/pve-no-subscription.sources" |
        awk -F'|' -v key="$1" -v suite="$(codename)" '
            $4 && $7 && $6 != key {
                split($2, suites, " ")
                for (i in suites) if (suites[i] == suite) bad = 1
            }
            END { exit bad }'
}

# A fresh install enables the subscription-only enterprise repos, so every
# apt update fails with 401 until they are off. Done when none is enabled and
# pve-no-subscription is, for the running suite.
check_repos() {
    local srcs key suite releases ceph
    key="$(repo_keyring)" || return 1
    signing_compatible "$key" || return 1
    managed_keyring_matches "$key" || return 1
    srcs="$(enabled_sources)"
    ! has_enterprise <<<"$srcs" || return 1
    suite="$(codename)"
    has_source http://download.proxmox.com/debian/pve "$suite" pve-no-subscription || return 1
    releases="$(ceph_releases)" || return 1
    for ceph in $releases; do
        has_source "http://download.proxmox.com/debian/ceph-$ceph" "$suite" no-subscription || return 1
    done
    return 0
}
default_repos() { echo yes; }
do_repos() {
    local suite f tmp ceph releases key new=""
    suite="$(codename)"
    if [ -z "$suite" ]; then
        echo "no VERSION_CODENAME in $OS_RELEASE" >&2
        return 1
    fi
    for f in "$APT_DIR/debian.sources" "$P/etc/apt/sources.list"; do
        if source_file read "$f" | has_enterprise; then
            echo "unsupported enterprise repository in protected file: $f" >&2
            return 1
        fi
    done
    key="$(repo_keyring)" || return 1
    signing_compatible "$key" || return 1
    releases="$(ceph_releases)"
    if ! managed_keyring_matches "$key"; then
        f="$APT_DIR/pve-no-subscription.sources"
        tmp="$(mktemp)"
        source_file keyring "$f" "$key" >"$tmp"
        cat "$tmp" >"$f"
        rm -f "$tmp"
    fi
    for f in "$APT_DIR"/*.sources "$APT_DIR"/*.list; do
        [ "$f" != "$APT_DIR/debian.sources" ] || continue
        if source_file read "$f" | has_enterprise; then
            tmp="$(mktemp)"
            source_file disable "$f" >"$tmp"
            cat "$tmp" >"$f"
            rm -f "$tmp"
        fi
    done
    # Add only what no enabled source already provides, so a rerun (or a repo
    # added by hand or in the web UI) never gets a duplicate.
    if ! has_source http://download.proxmox.com/debian/pve "$suite" pve-no-subscription; then
        new="Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: $suite
Components: pve-no-subscription
Signed-By: $key
"
    fi
    for ceph in $releases; do
        if ! has_source "http://download.proxmox.com/debian/ceph-$ceph" "$suite" no-subscription; then
            new="${new:+$new
}Types: deb
URIs: http://download.proxmox.com/debian/ceph-$ceph
Suites: $suite
Components: no-subscription
Signed-By: $key
"
        fi
    done
    if [ -n "$new" ]; then
        f="$APT_DIR/pve-no-subscription.sources"
        if [ -s "$f" ]; then new="

$new"; fi
        printf '%s' "$new" >>"$f"
    fi
    apt-get update
    log "pve-no-subscription is not recommended for production by Proxmox; with a subscription, re-enable the enterprise repos"
}

# Fingerprints (SHA256:...) of the keys in a key file, one per line.
fingerprints() { ssh-keygen -l -f "$1" 2>/dev/null | awk '{print $2}'; }

# Operator fingerprints whose key is still in authorized_keys.
operator_keys_present() {
    local fp
    [ -s "$OPERATOR_FPS" ] || return 0
    while read -r fp; do
        if fingerprints "$AUTH_KEYS" | grep -qxF "$fp"; then echo "$fp"; fi
    done <"$OPERATOR_FPS"
}

# Done when a key the operator supplied is in authorized_keys. With
# PVE_OPERATOR_KEY set, that key specifically, so a second key can be added.
check_ssh_keys() {
    local tmp fp
    if [ -n "${PVE_OPERATOR_KEY:-}" ]; then
        tmp="$(mktemp)"
        printf '%s\n' "$PVE_OPERATOR_KEY" >"$tmp"
        fp="$(fingerprints "$tmp")"
        rm -f "$tmp"
        [ -n "$fp" ] && fingerprints "$AUTH_KEYS" | grep -qxF "$fp" &&
            grep -qxF "$fp" "$OPERATOR_FPS" 2>/dev/null
        return
    fi
    [ -n "$(operator_keys_present)" ]
}
default_ssh_keys() { echo yes; }

# Append one public key unless its key material is already there, and record
# its fingerprint as an operator key. Appends through the Proxmox symlink
# (authorized_keys -> /etc/pve/priv/authorized_keys); never replaces it.
add_operator_key() {
    local key="$1" tmp fp
    tmp="$(mktemp)"
    printf '%s\n' "$key" >"$tmp"
    fp="$(fingerprints "$tmp")"
    rm -f "$tmp"
    if [ -z "$fp" ]; then
        echo "not a public key, ignored" >&2
        return 0
    fi
    if fingerprints "$AUTH_KEYS" | grep -qxF "$fp"; then
        log "already present: $fp"
    else
        printf '%s\n' "$key" >>"$AUTH_KEYS"
        log "added $fp"
    fi
    grep -qxF "$fp" "$OPERATOR_FPS" 2>/dev/null || echo "$fp" >>"$OPERATOR_FPS"
}

do_ssh_keys() {
    local key
    mkdir -p "$(dirname "$AUTH_KEYS")"
    touch "$AUTH_KEYS"
    if [ -n "${PVE_OPERATOR_KEY:-}" ]; then
        add_operator_key "$PVE_OPERATOR_KEY"
    elif [ "$OPT_YES" = 1 ]; then
        echo "--yes needs your public key in PVE_OPERATOR_KEY" >&2
        return 1
    else
        while :; do
            read -r -p "paste your public key (blank to finish): " key </dev/tty
            [ -n "$key" ] || break
            add_operator_key "$key"
        done
    fi
    chmod 700 "$(dirname "$AUTH_KEYS")"
    chmod 600 "$AUTH_KEYS"
    if [ -f "$OPERATOR_FPS" ]; then chmod 600 "$OPERATOR_FPS"; fi
}

# sshd logs "Accepted publickey for root from ... SHA256:..." per key login,
# so the journal proves an operator key really logs in. The unit covers both
# sshd and sshd-session (OpenSSH 9.8+).
operator_login_seen() {
    local fp accepted
    accepted="$(journalctl -u ssh.service --no-pager -o cat 2>/dev/null | grep 'Accepted publickey for root ' || true)"
    for fp in $(operator_keys_present); do
        if grep -qF "$fp" <<<"$accepted"; then return 0; fi
    done
    return 1
}

# The policy that counts is what sshd resolves, not what our file says: the
# first value wins, so an earlier include or sshd_config line can override it.
# ponytail: evaluates root from localhost only; a Match block for other
# addresses is not checked.
sshd_policy_ok() {
    local eff
    eff="$(sshd -T -C user=root,host=localhost,addr=127.0.0.1 2>/dev/null)" || return 1
    grep -Eqx 'permitrootlogin (prohibit-password|without-password)' <<<"$eff" &&
        grep -qx 'pubkeyauthentication yes' <<<"$eff" &&
        grep -qx 'passwordauthentication no' <<<"$eff" &&
        grep -qx 'kbdinteractiveauthentication no' <<<"$eff"
}

# Put the managed drop-in back how it was before this run ($1: backup copy,
# empty if there was none).
restore_dropin() {
    if [ -n "$1" ]; then
        mv "$1" "$SSHD_DROPIN"
    else
        rm -f "$SSHD_DROPIN"
    fi
}

# Root by key only. Never PermitRootLogin no: the web UI shell, migration and
# clustering log in as root over SSH.
check_ssh_harden() { sshd_policy_ok; }
default_ssh_harden() { echo yes; }
do_ssh_harden() {
    local backup=""
    if [ -z "$(operator_keys_present)" ]; then
        echo "refusing: no operator key in $AUTH_KEYS (Proxmox's own node keys do not count) - run ssh-keys first" >&2
        return 1
    fi
    if ! operator_login_seen && [ "$OPT_YES" != 1 ]; then
        read -r -p "log in as root with your key from a NEW terminal now, then press Enter " _ </dev/tty
    fi
    if ! operator_login_seen; then
        echo "refusing: sshd has logged no login with your operator key - ssh in as root with it first" >&2
        return 1
    fi
    if [ -f "$SSHD_DROPIN" ]; then
        backup="$(mktemp)"
        cp -p "$SSHD_DROPIN" "$backup"
    fi
    cat >"$SSHD_DROPIN" <<'EOF'
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
    if ! sshd -t; then
        restore_dropin "$backup"
        return 1
    fi
    if ! sshd_policy_ok; then
        echo "an earlier sshd setting overrides $SSHD_DROPIN; find it with: grep -rniE 'PermitRootLogin|PubkeyAuth|PasswordAuth|KbdInteractive' /etc/ssh/" >&2
        restore_dropin "$backup"
        return 1
    fi
    if ! systemctl reload ssh; then
        restore_dropin "$backup"
        return 1
    fi
    rm -f "$backup"
    log "verify a new key-based session before closing this one"
}

check_tailscale() { tailscale status >/dev/null 2>&1; }
default_tailscale() { echo yes; }
do_tailscale() {
    curl -fsSL https://tailscale.com/install.sh | sh
    tailscale up  # prints a URL to open
}

# The routes this node advertises, one per line, from the AdvertiseRoutes
# pref. Perl because every Proxmox install has it; jq is not guaranteed.
advertised_routes() {
    tailscale debug prefs 2>/dev/null |
        perl -MJSON::PP -0777 -ne 'print "$_\n" for @{ decode_json($_)->{AdvertiseRoutes} || [] }'
}

# Live forwarding, the persisted setting, a connected client and the route in
# AdvertiseRoutes. Approving it in the admin console stays a manual step.
check_subnet_router() {
    [ "$(sysctl -n net.ipv4.ip_forward 2>/dev/null)" = 1 ] &&
        grep -Eqx '[[:space:]]*net\.ipv4\.ip_forward[[:space:]]*=[[:space:]]*1[[:space:]]*' "$SYSCTL_FILE" 2>/dev/null &&
        tailscale status >/dev/null 2>&1 &&
        advertised_routes | grep -qxF "$LAN_ROUTE"
}
default_subnet_router() { echo no; }
do_subnet_router() {
    local routes tmp
    if ! tailscale status >/dev/null 2>&1; then
        echo "tailscale is not connected - run the tailscale step first" >&2
        return 1
    fi
    # `set` changes one pref and keeps the rest; `up` would want every flag.
    if ! tailscale set --help >/dev/null 2>&1; then
        echo "this tailscale has no 'tailscale set' - upgrade it: curl -fsSL https://tailscale.com/install.sh | sh" >&2
        return 1
    fi
    # Add the LAN to what is advertised already, never replace it. The exit
    # node routes (/0) belong to --advertise-exit-node, which set preserves.
    routes="$({
        advertised_routes | grep -vxE '0\.0\.0\.0/0|::/0' || true
        echo "$LAN_ROUTE"
    } | awk '!seen[$0]++' | paste -sd, -)"
    # IPv4 only: the route is IPv4, and IPv6 forwarding would stop the host
    # accepting router advertisements. Other lines in the file are kept.
    mkdir -p "$(dirname "$SYSCTL_FILE")"
    tmp="$(mktemp)"
    {
        grep -Ev '^[[:space:]]*net\.ipv4\.ip_forward[[:space:]]*=' "$SYSCTL_FILE" 2>/dev/null || true
        echo 'net.ipv4.ip_forward = 1'
    } >"$tmp"
    cat "$tmp" >"$SYSCTL_FILE"
    rm -f "$tmp"
    sysctl -w net.ipv4.ip_forward=1
    tailscale set --advertise-routes="$routes"
    log "approve $LAN_ROUTE in the Tailscale admin console: Machines -> $(hostname) -> Edit route settings"
}

# ShellFish widget (docs/08-shellfish-widgets.md): cron pushes this host's name,
# CPU, CPU temperature, memory and disk usage to the iPhone. ShellFish writes
# /root/.shellfishrc when Shell Integration is installed from the app while
# connected as root; until then there is nothing here but guidance. Nothing is
# cloned onto the hypervisor, so the widget script is downloaded to
# /usr/local/bin; an existing copy there is kept (see the doc to update it).
shellfish_tools() { have openssl && have xxd && have curl && have crontab; }
check_shellfish() {
    [ -e "$SHELLFISHRC" ] || return 3
    shellfish_tools && [ -x "$WIDGET_BIN" ] || return 1
    crontab -l 2>/dev/null | grep -qF "$WIDGET_BIN"
}
default_shellfish() { echo yes; }
do_shellfish() {
    local current tmp
    if [ ! -e "$SHELLFISHRC" ]; then
        log "in ShellFish on the iPhone, connected to this host as root: server settings -> Install Shell Integration, then rerun"
        log "see docs/08-shellfish-widgets.md"
        return 0
    fi
    if ! shellfish_tools; then
        # The repos step fixes the enterprise repository that makes update fail.
        apt-get update
        apt-get install -y openssl xxd curl cron
    fi
    if [ ! -e "$WIDGET_BIN" ] && [ ! -L "$WIDGET_BIN" ]; then
        tmp="$(mktemp)"
        # A failed or truncated download is never installed.
        if ! curl -fsSL "$WIDGET_URL" -o "$tmp" || ! bash -n "$tmp"; then
            rm -f "$tmp"
            echo "could not download $WIDGET_URL" >&2
            return 1
        fi
        mkdir -p "$(dirname "$WIDGET_BIN")"
        install -m 755 "$tmp" "$WIDGET_BIN"
        rm -f "$tmp"
    elif [ ! -x "$WIDGET_BIN" ]; then
        echo "preserving existing non-executable $WIDGET_BIN; fix or remove it and rerun" >&2
        return 1
    fi
    # Keep every existing entry; an unreadable crontab is not an empty one.
    if ! current="$(crontab -l 2>&1)"; then
        if [[ "$current" != "no crontab for "* ]]; then
            echo "cannot read crontab, leaving it alone: $current" >&2
            return 1
        fi
        current=""
    fi
    if ! grep -qF "$WIDGET_BIN" <<<"$current"; then
        printf '%s%s\n' "${current:+$current$'\n'}" \
            "$(printf '*/15 * * * * %q >/dev/null 2>&1' "$WIDGET_BIN")" | crontab -
    fi
    "$WIDGET_BIN"
    log "widget sent; add a ShellFish widget on the iPhone Home Screen if you have not"
}

# --- runner -----------------------------------------------------------------

parse_args() {
    OPT_STATUS=0
    OPT_YES=0
    OPT_HELP=0
    OPT_ONLY=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --status) OPT_STATUS=1 ;;
            --yes) OPT_YES=1 ;;
            --only)
                if [ $# -lt 2 ]; then
                    echo "--only needs a step list, e.g. --only ssh-keys,ssh-harden" >&2
                    usage >&2
                    exit 2
                fi
                OPT_ONLY="$2"
                shift
                ;;
            --help) OPT_HELP=1 ;;
            *)
                echo "unknown option: $1" >&2
                usage >&2
                exit 2
                ;;
        esac
        shift
    done
}

# Fill SELECTED from STEPS, honouring --only and its ordering.
select_steps() {
    local want step found
    SELECTED=("${STEPS[@]}")
    if [ -z "$OPT_ONLY" ]; then
        return 0
    fi
    SELECTED=()
    for want in ${OPT_ONLY//,/ }; do
        found=0
        for step in "${STEPS[@]}"; do
            if [ "$step" = "$want" ]; then
                found=1
            fi
        done
        if [ "$found" = 0 ]; then
            echo "unknown step: $want (have: ${STEPS[*]})" >&2
            exit 2
        fi
    done
    for step in "${STEPS[@]}"; do
        case ",$OPT_ONLY," in
            *",$step,"*) SELECTED+=("$step") ;;
        esac
    done
}

# Ask about one step. Returns 0 to run it, 1 to skip.
prompt() {
    local step="$1" default="$2" hint reply
    if [ "$default" = yes ]; then hint="[Y/n]"; else hint="[y/N]"; fi
    read -r -p "$step $hint " reply </dev/tty
    if [ -z "$reply" ]; then
        reply="$default"
    fi
    case "$reply" in
        [Yy]*) return 0 ;;
        *) return 1 ;;
    esac
}

next_steps() {
    log "done. Next:"
    echo "  open the Tailscale login URL printed above, if one was"
    if have tailscale; then
        echo "  record $(tailscale ip -4 2>/dev/null || echo 'the 100.x IP') for pve1 in README.md Local Machines"
    fi
}

main() {
    parse_args "$@"
    if [ "$OPT_HELP" = 1 ]; then
        usage
        return 0
    fi
    require_pve
    select_steps

    local step rc status
    local todo=()
    printf '%-14s %s\n' STEP STATUS
    for step in "${SELECTED[@]}"; do
        rc=0
        "$(fname check "$step")" || rc=$?
        case "$rc" in
            0) status="done" ;;
            2) status=n/a ;;
            3) status=manual; todo+=("$step") ;;
            *)
                status=todo
                todo+=("$step")
                ;;
        esac
        printf '%-14s %s\n' "$step" "$status"
    done

    if [ "$OPT_STATUS" = 1 ] || [ "${#todo[@]}" -eq 0 ]; then
        return 0
    fi
    if [ "$OPT_YES" != 1 ] && ! { : </dev/tty; } 2>/dev/null; then
        echo "no terminal to prompt on - rerun with --yes" >&2
        return 1
    fi

    # Steps run in this shell, as in setup.sh. errexit still applies inside a
    # step; this trap turns a failure into "step failed". Command substitutions
    # inherit the trap (set -E) but not errexit, so only a failure in the main
    # shell counts.
    trap 'rc=$?; if [ "$BASH_SUBSHELL" = 0 ]; then echo "step failed: $step" >&2; exit 1; fi' ERR
    for step in "${todo[@]}"; do
        if [ "$OPT_YES" != 1 ] && ! prompt "$step" "$("$(fname default "$step")")"; then
            continue
        fi
        log "$step"
        "$(fname "do" "$step")"
    done
    next_steps
}

main "$@"
