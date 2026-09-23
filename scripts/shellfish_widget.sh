#!/usr/bin/env bash
# shellfish_widget.sh - push CPU, CPU temperature, memory and disk usage to the
# Secure ShellFish widget on the iPhone (docs/08-shellfish-widgets.md).
#
#   scripts/shellfish_widget.sh                  # disk usage of /
#   scripts/shellfish_widget.sh / /media/Drive1  # one entry per mount point
#   scripts/shellfish_widget.sh --print          # show the arguments, send nothing
#
# setup-machine --only shellfish runs it from cron. Cron shells do not read
# ~/.bashrc past its interactive guard, so ~/.shellfishrc is sourced here.
set -euo pipefail

usage() {
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
}

# Busy share of all CPUs over one second, from two /proc/stat samples.
cpu_percent() {
    local a b
    read -r -a a </proc/stat
    sleep 1
    read -r -a b </proc/stat
    awk -v a="${a[*]}" -v b="${b[*]}" 'BEGIN {
        n = split(a, x, " "); split(b, y, " ")
        for (i = 2; i <= n; i++) {
            d = y[i] - x[i]; total += d
            if (i == 5 || i == 6) idle += d  # idle, iowait
        }
        printf "%d%%\n", total ? 100 * (total - idle) / total + 0.5 : 0
    }'
}

# The CPU package sensor if there is one (Intel coretemp, AMD k10temp),
# otherwise the first readable temperature. Prints nothing when there is none,
# which is normal in a VM.
cpu_temp() {
    local t name label best="" first=""
    for t in /sys/class/hwmon/hwmon*/temp*_input; do
        [ -r "$t" ] || continue
        name=$(cat "${t%/*}/name" 2>/dev/null) || name=""
        label=$(cat "${t%_input}_label" 2>/dev/null) || label=""
        case "$name:$label" in
            coretemp:Package*|k10temp:Tctl|k10temp:Tdie|zenpower:Tdie) best=$t; break ;;
        esac
        [ -n "$first" ] || first=$t
    done
    t=${best:-$first}
    [ -n "$t" ] || return 0
    awk '{ printf "%d°C\n", $1 / 1000 + 0.5 }' "$t"
}

mem_percent() {
    awk '/^MemTotal:/ { t = $2 } /^MemAvailable:/ { a = $2 }
        END { printf "%d%%\n", t ? 100 * (t - a) / t + 0.5 : 0 }' /proc/meminfo
}

disk_percent() {
    df -P "$1" | awk 'NR == 2 { print $5 }'
}

main() {
    local print=0 mounts=() mount label temp
    while [ $# -gt 0 ]; do
        case "$1" in
            --print) print=1 ;;
            --help|-h) usage; return 0 ;;
            -*) echo "unknown option: $1" >&2; return 2 ;;
            *) mounts+=("$1") ;;
        esac
        shift
    done
    [ "${#mounts[@]}" -gt 0 ] || mounts=(/)

    local args=(cpu.fill "$(cpu_percent)" CPU)
    temp=$(cpu_temp)
    args+=(thermometer.medium "${temp:-n/a}" Temp)
    args+=(memorychip "$(mem_percent)" Mem)
    for mount in "${mounts[@]}"; do
        if [ "$mount" = / ]; then label=Disk; else label=${mount##*/}; fi
        args+=(internaldrive "$(disk_percent "$mount")" "$label")
    done

    if [ "$print" = 1 ]; then
        printf '%s\n' "${args[*]}"
        return 0
    fi

    if [ ! -r "$HOME/.shellfishrc" ]; then
        echo "no ~/.shellfishrc - install Shell Integration from the ShellFish app first" >&2
        return 1
    fi
    # widget() encrypts with these; without xxd it sends garbage and exits 0.
    local tool
    for tool in openssl xxd curl; do
        if ! command -v "$tool" >/dev/null; then
            echo "$tool is missing - run: setup-machine --only shellfish" >&2
            return 1
        fi
    done
    # ShellFish's file reads unset variables ($TMUX, $SSH_TTY...) and is not
    # written for errexit, so load and run it with both off.
    set +eu
    # shellcheck source=/dev/null
    . "$HOME/.shellfishrc"
    widget "${args[@]}"
}

main "$@"
