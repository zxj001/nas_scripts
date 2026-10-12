#!/usr/bin/env bash
# Standalone repository repair for Proxmox VE 9; does not install upgrades.
# Definitions precede main so an incomplete download cannot start editing APT.
set -euo pipefail

# Test hook: redirect host paths into a fixture; unset on a real host.
P="${PVE_CLEANER_PREFIX:-}"
DIR="$P/etc/apt/sources.list.d"
KEY=/usr/share/keyrings/proxmox-archive-keyring.gpg

# Read both APT formats, or disable only PVE/Ceph enterprise entries. The
# deb822 field/continuation handling follows setup_adapters/pve_repos.sh.
source_file() {
    local mode="$1" file="$2"
    [[ -f "$file" ]] || return 0
    awk -v mode="$mode" -v format="${file##*.}" '
        function enterprise(uri) {
            return uri ~ /^https?:\/\/enterprise\.proxmox\.com\/debian\/(pve\/?|ceph-[a-z]+\/?)$/
        }
        function field() {
            if (k == "types") types = v
            else if (k == "uris") u = v
            else if (k == "suites") s = v
            else if (k == "components") c = v
            else if (k == "signed-by") signed = v
            else if (k == "enabled") en = (tolower(v) !~ /^(no|false|off|0|disable)$/)
        }
        function flush(    i, count, uris, ent, other, skip) {
            field()
            count = split(u, uris, " ")
            for (i = 1; i <= count; i++) {
                if (enterprise(uris[i])) ent = 1
                else other = 1
            }
            if (mode == "read") {
                if (u != "") print u "|" s "|" c "|" en "|" ent "|" signed "|" types "|" FILENAME
            } else if (en && ent && other) {
                print "Mixed enterprise and unrelated URIs in " FILENAME "; split the stanza first." > "/dev/stderr"
                exit 1
            } else {
                for (i = 1; i <= n; i++) {
                    if (buf[i] ~ /^[[:space:]]*#/) { print buf[i]; continue }
                    if (buf[i] !~ /^[[:space:]]/)
                        skip = (ent && en && tolower(buf[i]) ~ /^enabled[[:space:]]*:/)
                    if (!skip) print buf[i]
                }
                if (ent && en) print "Enabled: no"
            }
            u = s = c = signed = types = k = v = ""; en = 1; n = 0
        }
        BEGIN { en = 1 }
        format != "sources" {
            raw = $0
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
                if (mode == "read") print $2 "|" $3 "|" c "|1|" enterprise($2) "|" signed "|" $1 "|" FILENAME
                else if (enterprise($2)) raw = "# " raw
            }
            if (mode != "read") print raw
            next
        }
        /^[[:space:]]*$/ { flush(); if (mode != "read") print; next }
        { buf[++n] = $0 }
        /^[[:space:]]*#/ { next }
        /^[[:space:]]/ { sub(/^[[:space:]]+/, ""); v = v (v == "" ? "" : " ") $0; next }
        {
            field()
            k = tolower($0); sub(/[[:space:]]*:.*/, "", k)
            v = $0; sub(/^[^:]*:[[:space:]]*/, "", v)
        }
        END { if (format == "sources") flush() }' "$file"
}

has_source() {
    awk -F'|' -v uri="$1" -v component="$2" '
        $4 && $7 ~ /(^|[[:space:]])deb([[:space:]]|$)/ {
            split($1, us, " "); split($2, ss, " "); split($3, cs, " ")
            hu = hs = hc = 0
            for (i in us) {
                sub(/\/$/, "", us[i]); sub(/^https:/, "http:", us[i])
                if (us[i] == uri) hu = 1
            }
            for (i in ss) if (ss[i] == "trixie") hs = 1
            for (i in cs) if (cs[i] == component) hc = 1
            if (hu && hs && hc) found = 1
        }
        END { exit !found }'
}

main() {
    local version suite file name backup records work uri component ceph index
    local -a files=() repositories=(http://download.proxmox.com/debian/pve)
    if [[ "$(id -u)" != 0 ]]; then
        echo "Run this script as root." >&2
        return 1
    fi
    version="$(pveversion)" || return 1
    suite="$(sed -n 's/^VERSION_CODENAME=//p' "$P/etc/os-release" | tr -d '\"')"
    if [[ "$version" != pve-manager/9.* || "$suite" != trixie || ! -d "$P/etc/pve" ]]; then
        echo "This script requires Proxmox VE 9 on Debian trixie." >&2
        return 1
    fi
    if [[ ! -s "$P$KEY" ]] || ! runuser -u _apt -- test -r "$P$KEY"; then
        echo "Missing, empty, or unreadable Proxmox archive keyring: $KEY" >&2
        return 1
    fi

    # Only inspect files APT loads, not backups, disabled extensions or subdirs.
    for file in "$P/etc/apt/sources.list" "$DIR"/*.list "$DIR"/*.sources; do
        [[ -f "$file" ]] || continue
        name="${file##*/}"
        [[ "$name" != *[!a-zA-Z0-9_.-]* ]] || continue
        files+=("$file")
    done
    records="$(for file in ${files[@]+"${files[@]}"}; do source_file read "$file"; done)"

    # Prevent silently mixing release suites or introducing Signed-By conflicts.
    awk -F'|' -v key="$KEY" '
        $4 {
            split($1, uris, " "); split($2, suites, " ")
            for (i in uris) {
                if ($5 && uris[i] ~ /^https?:\/\/enterprise\.proxmox\.com\/debian\/ceph-[a-z]+\/?$/) {
                    for (j in suites) if (suites[j] != "trixie") {
                        print "Wrong-suite Ceph enterprise repository in " $8 "; review manually." > "/dev/stderr"
                        bad = 1
                    }
                }
                if (uris[i] !~ /^https?:\/\/download\.proxmox\.com\/debian\/(pve|ceph-[a-z]+)\/?$/) continue
                for (j in suites) if (suites[j] != "trixie") {
                    print "Wrong-suite public repository in " $8 "; review manually." > "/dev/stderr"
                    bad = 1
                }
                if ($6 != key) {
                    print "Conflicting or missing Signed-By in " $8 "; review manually." > "/dev/stderr"
                    bad = 1
                }
            }
        }
        END { exit bad }' <<<"$records"

    # Keep updates available for the configured Ceph release. Disabled historical
    # Ceph stanzas are deliberately left alone; no Ceph release upgrade is done.
    while IFS= read -r ceph; do
        [[ -n "$ceph" ]] && repositories+=("http://download.proxmox.com/debian/ceph-$ceph")
    done < <(awk -F'|' '$4 && $5 {
        split($1, uris, " ")
        for (i in uris) if (uris[i] ~ /\/ceph-[a-z]+\/?$/) {
            sub(/.*ceph-/, "", uris[i]); sub(/\/$/, "", uris[i]); print uris[i]
        }
    }' <<<"$records" | sort -u)

    mkdir -p "$P/root"
    backup="$(mktemp -d "$P/root/apt-repos-backup-$(date +%Y%m%d-%H%M%S)-XXXXXX")"
    # A failed copy must stop before any changes, even when sources.list exists.
    if [[ -e "$P/etc/apt/sources.list" ]]; then
        cp -a "$P/etc/apt/sources.list" "$backup/"
    fi
    if [[ -d "$DIR" ]]; then cp -a "$DIR" "$backup/"; fi
    echo "Backup saved: $backup"

    work="$(mktemp -d)"
    CLEANER_WORK="$work"
    trap 'rm -rf -- "$CLEANER_WORK"' EXIT
    # Stage every rewrite first: a refused mixed-URI stanza changes no sources.
    index=0
    for file in ${files[@]+"${files[@]}"}; do
        if awk -F'|' -v file="$file" '$4 && $5 && $8 == file { found = 1 } END { exit !found }' <<<"$records"; then
            source_file disable "$file" >"$work/$index"
        else
            cp -a "$file" "$work/$index"
        fi
        index=$((index + 1))
    done
    index=0
    for file in ${files[@]+"${files[@]}"}; do
        if ! cmp -s "$file" "$work/$index"; then
            cat "$work/$index" >"$file"
        fi
        index=$((index + 1))
    done
    mkdir -p "$DIR"
    for uri in "${repositories[@]}"; do
        component=no-subscription
        [[ "$uri" != */pve ]] || component=pve-no-subscription
        if has_source "$uri" "$component" <<<"$records"; then
            echo "Existing active $component repository: $uri"
            continue
        fi
        # Append separated stanzas; never overwrite an administrator source.
        file="$DIR/proxmox.sources"
        [[ ! -s "$file" ]] || printf '\n\n' >>"$file"
        cat >>"$file" <<EOF
Types: deb
URIs: $uri
Suites: trixie
Components: $component
Signed-By: $KEY
EOF
        chmod 644 "$file"
        echo "Added $component repository: $uri"
    done

    echo "Refreshing APT..."
    # APT otherwise returns success for some download failures with stale lists.
    apt-get update -o APT::Update::Error-Mode=any
    echo "Repository setup finished. Review upgrades with: apt -s full-upgrade"
}

main "$@"
