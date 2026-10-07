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
#   scripts/shellfish_widget.sh --help           # this text
#
# Cron runs it every 15 minutes by default. Manage that cron line with:
#
#   --install [options] [mounts]  copy the script to ~/.local/bin (root:
#       /usr/local/bin), add the cron line and send once. It also re-enables a
#       disabled widget, keeping its arguments unless new ones are given. The
#       line sends to the widget named after the short hostname unless
#       --target says otherwise (--target '' for the one shared widget).
#       It also enables and starts the cron service (sudo when not root), so
#       the widget keeps updating after a reboot.
#   --minutes N  with --install: minutes between runs, 15 by default. N divides
#       an hour (1-30) or is whole hours that divide a day (60, 120 ... 1440).
#       Alone with --install it changes only the schedule of the existing line.
#   --disable    comment the cron line out. setup-machine leaves it that way
#       until --install enables it again.
#   --uninstall  remove the cron line and the installed copy. setup-machine
#       --only shellfish puts both back; use --disable to keep it off.
#   --status     show whether the widget is installed, enabled or disabled,
#       its cron line, and whether cron and Shell Integration are ready.
#       Exits 0 when enabled, 3 when disabled or not installed.
#
# setup-machine --only shellfish also installs it. Cron shells do not read
# ~/.bashrc past its interactive guard, so ~/.shellfishrc is sourced here.
set -euo pipefail

# First line of this script; anything else at the install path is an operator file.
MARK="# shellfish_widget.sh - "
# Prefix of a cron line --disable turned off; setup_tasks/shellfish.py reads it too.
DISABLED="#shellfish-disabled# "

usage() {
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
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
# onto any line with room, so each line starts with a break. A color lasts
# until "foreground", so reset it before the icon.
metric() {
    printf '%s\n' --text '\n' foreground "$1" "$3" "$(level_color "$2" "$4" "$5")"
    case "$2" in *%) printf '%s\n' "$2" ;; esac
    printf '%s\n' --text " $2"
}

disk_percent() {
    df -P "$1" | awk 'NR == 2 { print $5 }'
}

# Where --install puts the script; setup_tasks/shellfish.py uses the same paths.
installed_path() {
    if [ "$(id -u)" = 0 ]; then echo /usr/local/bin/shellfish_widget.sh
    else echo "$HOME/.local/bin/shellfish_widget.sh"
    fi
}

read_crontab() {
    local out
    # An unreadable crontab is not an empty one; never overwrite it.
    if ! out=$(crontab -l 2>&1); then
        case "$out" in
            *"no crontab for"*) return 0 ;;
            *) echo "cannot read crontab: $out" >&2; return 1 ;;
        esac
    fi
    [ -z "$out" ] || printf '%s\n' "$out"
}

# Cron lines that run any copy of the widget script, live or disabled.
is_widget_line() {
    case "$1" in
        "$DISABLED"*) return 0 ;;
        \#*) return 1 ;;
        *shellfish_widget.sh*) return 0 ;;
        *) return 1 ;;
    esac
}

# The crontab without its widget lines.
other_lines() {
    local line
    while IFS= read -r line; do
        is_widget_line "$line" || printf '%s\n' "$line"
    done <<<"$1"
}

write_crontab() {
    if [ -z "$1" ]; then printf '' | crontab -
    else printf '%s\n' "$1" | crontab -
    fi
}

# The cron schedule for a run every $1 minutes; empty when cron can't keep
# the gaps even.
schedule() {
    local n=$1
    case "$n" in ''|*[!0-9]*|0*) return 0 ;; esac
    if [ "$n" -lt 60 ]; then
        [ $((60 % n)) = 0 ] || return 0
        if [ "$n" = 1 ]; then echo "* * * * *"; else echo "*/$n * * * *"; fi
    elif [ $((n % 60)) = 0 ] && [ $((1440 % n)) = 0 ]; then
        n=$((n / 60))
        case "$n" in
            1) echo "0 * * * *" ;;
            24) echo "0 0 * * *" ;;
            *) echo "0 */$n * * *" ;;
        esac
    fi
}

# Cron reads % as a newline, so escape it in the arguments.
# $1 is the schedule, $2 the script, the rest its arguments.
cron_line() {
    local path=$2 word line
    line="$1 $(printf '%q' "$path")"
    shift 2
    for word in "$@"; do line+=" $(printf '%q' "$word")"; done
    printf '%s >/dev/null 2>&1\n' "${line//%/\\%}"
}

# The cron service runs the widget line, so it has to start at boot.
enable_cron() {
    local unit sudo=()
    command -v systemctl >/dev/null || {
        echo "no systemd here; make sure cron starts at boot" >&2
        return 0
    }
    for unit in cron crond; do
        systemctl cat "$unit.service" >/dev/null 2>&1 || continue
        if systemctl is-enabled --quiet "$unit" && systemctl is-active --quiet "$unit"; then
            return 0
        fi
        [ "$(id -u)" = 0 ] || sudo=(sudo)
        "${sudo[@]}" systemctl enable --now "$unit"
        echo "enabled the $unit service, so the widget keeps updating after a reboot"
        return 0
    done
    echo "no cron service found; make sure cron starts at boot" >&2
}

# $1 is "replace" to write a new line from the other arguments, or "keep" to
# re-enable the widget lines already in the crontab. $2 is the schedule; empty
# keeps an existing line's.
do_install() {
    local mode=$1 when=$2 self dest tmp crontab line lines=() new=() tool
    shift 2
    # Check everything before changing anything.
    if [ ! -r "$HOME/.shellfishrc" ]; then
        echo "no ~/.shellfishrc - install Shell Integration from the ShellFish app first" >&2
        return 1
    fi
    for tool in openssl xxd curl crontab; do
        if ! command -v "$tool" >/dev/null; then
            echo "$tool is missing - run: setup-machine --only shellfish" >&2
            return 1
        fi
    done
    dest=$(installed_path)
    if [ -e "$dest" ] || [ -L "$dest" ]; then
        if [ -L "$dest" ] || [ ! -f "$dest" ] || ! head -n 3 "$dest" | grep -qF "$MARK"; then
            echo "$dest is not this script; move it aside first" >&2
            return 1
        fi
    fi
    crontab=$(read_crontab)

    self=$(readlink -f "${BASH_SOURCE[0]}")
    if [ "$self" != "$(readlink -f "$dest" 2>/dev/null || true)" ]; then
        mkdir -p "${dest%/*}"
        tmp=$(mktemp "${dest%/*}/.shellfish_widget.sh.XXXXXX")
        cp "$self" "$tmp"
        chmod 755 "$tmp"
        mv -f "$tmp" "$dest"
    fi

    while IFS= read -r line; do
        is_widget_line "$line" && lines+=("$line")
    done <<<"$crontab"
    if [ "$mode" = replace ] || [ "${#lines[@]}" -eq 0 ]; then
        new=("$(cron_line "${when:-*/15 * * * *}" "$dest" "$@")")
    else
        # Enable: uncomment the existing lines and point them at the installed copy.
        for line in "${lines[@]}"; do
            line=${line#"$DISABLED"}
            new+=("$(printf '%s\n' "$line" |
                sed -E "s#[^[:space:]]*shellfish_widget\.sh([[:space:]]|\$)#$(printf '%q' "$dest")\\1#")")
            if [ -n "$when" ]; then
                new[-1]=$(awk -v when="$when" '{ for (i = 1; i <= 5; i++) $i = ""; sub(/^ +/, ""); print when, $0 }' <<<"${new[-1]}")
            fi
        done
    fi
    crontab=$(other_lines "$crontab")
    [ -z "$crontab" ] || crontab+=$'\n'
    crontab+=$(printf '%s\n' "${new[@]}")
    write_crontab "$crontab"
    enable_cron
    printf 'installed %s; crontab:\n' "$dest"
    printf '  %s\n' "${new[@]}"
    # Send once now: the first line's command without its schedule and redirect.
    line=$(awk '{ for (i = 1; i <= 5; i++) $i = ""; sub(/^ +/, ""); print }' <<<"${new[0]}")
    line=${line% >/dev/null 2>&1}
    bash -c "${line//\\%/%}"
}

do_disable() {
    local crontab line out=() n=0
    crontab=$(read_crontab)
    while IFS= read -r line; do
        if is_widget_line "$line" && [ "${line#"$DISABLED"}" = "$line" ]; then
            line="$DISABLED$line"
            n=$((n + 1))
        fi
        out+=("$line")
    done <<<"$crontab"
    if [ "$n" = 0 ]; then
        if grep -qF "$DISABLED" <<<"$crontab"; then echo "widget is already disabled"
        else echo "no cron line runs the widget"
        fi
        return 0
    fi
    write_crontab "$(printf '%s\n' "${out[@]}")"
    echo "widget disabled; --install enables it again"
}

# "every 15 minutes" for a schedule cron_line writes, else the raw fields.
describe_schedule() {
    local n
    case "$1" in
        "* * * * *") echo "every minute" ;;
        "*/"*" * * * *") n=${1#\*/}; echo "every ${n%% *} minutes" ;;
        "0 * * * *") echo "every hour" ;;
        "0 0 * * *") echo "once a day" ;;
        "0 */"*" * * *") n=${1#0 \*/}; echo "every ${n%% *} hours" ;;
        *) echo "cron schedule: $1" ;;
    esac
}

do_status() {
    local crontab line dest live=() off=() unit state="enabled"
    dest=$(installed_path)
    crontab=$(read_crontab)
    while IFS= read -r line; do
        is_widget_line "$line" || continue
        if [ "${line#"$DISABLED"}" = "$line" ]; then live+=("$line"); else off+=("${line#"$DISABLED"}"); fi
    done <<<"$crontab"
    if [ "${#live[@]}" -gt 0 ]; then state=enabled
    elif [ "${#off[@]}" -gt 0 ]; then state="disabled (--install enables it)"
    else state="not installed (--install installs it)"
    fi
    echo "widget:       $state"
    for line in ${live[@]+"${live[@]}"} ${off[@]+"${off[@]}"}; do
        echo "runs:         $(describe_schedule "$(awk '{ print $1, $2, $3, $4, $5 }' <<<"$line")")"
        echo "cron line:    $line"
    done
    if [ -f "$dest" ] && head -n 3 "$dest" | grep -qF "$MARK"; then
        if cmp -s "$dest" "$(readlink -f "${BASH_SOURCE[0]}")"; then echo "script:       $dest"
        else echo "script:       $dest (differs from this copy; --install updates it)"
        fi
    elif [ -e "$dest" ]; then echo "script:       $dest is not this script"
    else echo "script:       not at $dest"
    fi
    if [ -r "$HOME/.shellfishrc" ]; then echo "integration:  ~/.shellfishrc found"
    else echo "integration:  no ~/.shellfishrc - install Shell Integration from the ShellFish app"
    fi
    if command -v systemctl >/dev/null; then
        for unit in cron crond; do
            systemctl cat "$unit.service" >/dev/null 2>&1 || continue
            echo "cron service: $unit $(systemctl is-enabled "$unit" 2>/dev/null || true), $(systemctl is-active "$unit" 2>/dev/null || true)"
            break
        done
    fi
    [ "${#live[@]}" -gt 0 ] || return 3
}

do_uninstall() {
    local crontab others dest
    crontab=$(read_crontab)
    others=$(other_lines "$crontab")
    if [ "$others" != "$crontab" ]; then
        write_crontab "$others"
        echo "removed the widget cron line"
    fi
    dest=$(installed_path)
    if [ -f "$dest" ] && [ ! -L "$dest" ] && head -n 3 "$dest" | grep -qF "$MARK"; then
        rm -f "$dest"
        echo "removed $dest"
    fi
}

main() {
    local print=0 mounts=() mount label temp name target="" action="" options=()
    local target_set=0 given=0 minutes="" when=""
    name=$(hostname -s 2>/dev/null || hostname)
    while [ $# -gt 0 ]; do
        case "$1" in
            --print) print=1 ;;
            --target)
                [ $# -ge 2 ] || { echo "--target needs a widget identifier" >&2; return 2; }
                target=$2
                target_set=1
                given=1
                # --target '' is the shared widget: no --target in the cron line.
                [ -z "$2" ] || options+=("$1" "$2")
                shift
                ;;
            --name)
                [ $# -ge 2 ] || { echo "--name needs a title" >&2; return 2; }
                name=$2
                given=1
                options+=("$1" "$2")
                shift
                ;;
            --minutes)
                [ $# -ge 2 ] || { echo "--minutes needs a number" >&2; return 2; }
                minutes=$2
                when=$(schedule "$2")
                [ -n "$when" ] || {
                    echo "--minutes takes a number that divides an hour (1-30) or whole hours that divide a day (60-1440)" >&2
                    return 2
                }
                shift
                ;;
            --install|--uninstall|--disable|--status)
                [ -z "$action" ] || { echo "use one of --install, --uninstall, --disable, --status" >&2; return 2; }
                action=${1#--}
                ;;
            --help|-h) usage; return 0 ;;
            -*) echo "unknown option: $1" >&2; return 2 ;;
            *) mounts+=("$1"); options+=("$1"); given=1 ;;
        esac
        shift
    done
    if [ -n "$minutes" ] && [ "$action" != install ]; then
        echo "--minutes goes with --install" >&2
        return 2
    fi
    if [ -n "$action" ] && [ "$print" = 1 ]; then
        echo "--print does not go with --$action" >&2
        return 2
    fi
    case "$action" in
        install)
            local mode=keep
            [ "$given" = 0 ] || mode=replace
            # Same default as setup-machine: this machine's own widget.
            if [ "$target_set" = 0 ]; then
                options=(--target "$(hostname -s 2>/dev/null || hostname)" "${options[@]}")
            fi
            do_install "$mode" "$when" "${options[@]}"
            return
            ;;
        uninstall|disable|status)
            [ "$given" = 0 ] || { echo "--$action takes no other arguments" >&2; return 2; }
            "do_$action"
            return
            ;;
    esac
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
