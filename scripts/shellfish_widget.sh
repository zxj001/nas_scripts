#!/usr/bin/env bash
# shellfish_widget.sh - push this machine's name, CPU, CPU temperature, memory
# and disk usage to the Secure ShellFish widget on the iPhone
# (docs/08-shellfish-widgets.md). Values are green, orange from 75% (70°C) and
# red from 90% (85°C). Temp is left out when there is no sensor, as in a VM.
#
#   scripts/shellfish_widget.sh                  # disk usage of /
#   scripts/shellfish_widget.sh / /media/Drive1  # one entry per mount point
#   scripts/shellfish_widget.sh --name NAS       # title instead of the hostname
#   scripts/shellfish_widget.sh --target pve1    # a widget of its own (ShellFish Pro)
#   scripts/shellfish_widget.sh --print          # show the arguments, send nothing
#
# setup-machine --only shellfish runs it from cron. Cron shells do not read
# ~/.bashrc past its interactive guard, so ~/.shellfishrc is sourced here.
set -euo pipefail

usage() {
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
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

GREEN='#34c759' ORANGE='#ff9f0a' RED='#ff3b30'

# Color for a value such as "83%" or "71°C": green, orange from $2, red from $3.
level_color() {
    local n=${1%%[!0-9]*}
    if [ "${n:-0}" -ge "$3" ]; then echo "$RED"
    elif [ "${n:-0}" -ge "$2" ]; then echo "$ORANGE"
    else echo "$GREEN"
    fi
}

# One line per sensor: icon, label, then a colored bar and value ("83%") or
# just the colored value ("71°C"). The widget's floating layout packs items
# onto any line with room, so each line starts with a break.
metric() {
    printf '%s\n' --text '\n' "$1" foreground "$3" "$(level_color "$2" "$4" "$5")"
    case "$2" in *%) printf '%s\n' "$2" ;; esac
    printf '%s\n' --text " $2"
}

disk_percent() {
    df -P "$1" | awk 'NR == 2 { print $5 }'
}

main() {
    local print=0 mounts=() mount label temp name target=""
    name=$(hostname -s 2>/dev/null || hostname)
    while [ $# -gt 0 ]; do
        case "$1" in
            --print) print=1 ;;
            --target)
                [ $# -ge 2 ] || { echo "--target needs a widget identifier" >&2; return 2; }
                target=$2
                shift
                ;;
            --name)
                [ $# -ge 2 ] || { echo "--name needs a title" >&2; return 2; }
                name=$2
                shift
                ;;
            --help|-h) usage; return 0 ;;
            -*) echo "unknown option: $1" >&2; return 2 ;;
            *) mounts+=("$1") ;;
        esac
        shift
    done
    [ "${#mounts[@]}" -gt 0 ] || mounts=(/)

    # --text so a name like "100%" or "#1" is never read as progress or color.
    local args=(server.rack --text "$name")
    # Pro: the widget configured with this identifier gets this machine's data.
    if [ -n "$target" ]; then args=(--target "$target" "${args[@]}"); fi
    mapfile -t -O "${#args[@]}" args < <(metric cpu.fill "$(cpu_percent)" CPU 75 90)
    temp=$(cpu_temp)
    if [ -n "$temp" ]; then
        mapfile -t -O "${#args[@]}" args < <(metric thermometer.medium "$temp" Temp 70 85)
    fi
    mapfile -t -O "${#args[@]}" args < <(metric memorychip "$(mem_percent)" Mem 75 90)
    for mount in "${mounts[@]}"; do
        if [ "$mount" = / ]; then label=Disk; else label=${mount##*/}; fi
        mapfile -t -O "${#args[@]}" args < <(metric internaldrive "$(disk_percent "$mount")" "$label" 75 90)
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
