#!/usr/bin/env bash
# Proxmox apt repositories: disable the subscription-only enterprise repos and
# enable pve-no-subscription (and the matching Ceph repo) for the running
# suite. Called by setup_tasks/repos.py:
#
#   pve_repos.sh probe   exit 0 = configured, 1 = work remains
#   pve_repos.sh apply   make it so, then apt-get update
#
# Shell because the deb822/.list parsing below is the version proven by
# tests/test_pve_repos.sh; the Python task only maps its result.
set -Eeuo pipefail

# Test hook: tests/test_pve_repos.sh points every path into a fixture.
P="${PVE_SETUP_PREFIX:-}"
APT_DIR="$P/etc/apt/sources.list.d"
OS_RELEASE="$P/etc/os-release"

log() { printf '==> %s\n' "$*"; }

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

pve_repos_main() {
    case "${1:-}" in
        probe) check_repos ;;
        apply) do_repos ;;
        *) echo "usage: pve_repos.sh probe|apply" >&2; return 2 ;;
    esac
}

pve_repos_main "$@"
