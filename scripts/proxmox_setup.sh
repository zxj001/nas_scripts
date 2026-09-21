#!/usr/bin/env bash
# proxmox_setup - SSH keys, SSH hardening and Tailscale on a Proxmox VE host,
# the way docs/pve-host.md describes. The host-side analogue of setup.sh.
#
#   curl -fsSL https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/proxmox_setup.sh | bash
#
# Rerunning that line is the update: nothing is cloned onto the hypervisor.
# Everything below is a definition; the last line calls main, so a half
# downloaded copy does nothing.
set -Eeuo pipefail

LAN_ROUTE="192.168.1.0/24"

# Ordered step registry, same contract as setup.sh. Every name here needs
# three functions, with "-" replaced by "_":
#
#   check_<name>    0 = done, 1 = todo, 2 = not applicable
#   do_<name>       make it so
#   default_<name>  echo yes|no - the prompt default
#
# tests/test_setup.sh fails if a registered step is missing one.
#
# Nothing else belongs here: no upgrade, guest agent, sleep or dev tools on the
# hypervisor. Proxmox manages its own packages.
STEPS=(ssh-keys ssh-harden tailscale subnet-router)

usage() {
    cat <<'EOF'
Usage: proxmox_setup.sh [--status] [--yes] [--only a,b] [--help]

  --status     run the checks, print the table and exit
  --yes        run every not-done step without prompting
  --only a,b   only these steps, comma separated
  --help       this text
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

check_ssh_keys() { [ -s /root/.ssh/authorized_keys ]; }
default_ssh_keys() { echo yes; }
do_ssh_keys() {
    local key tmp
    mkdir -p /root/.ssh
    touch /root/.ssh/authorized_keys
    tmp="$(mktemp)"
    while :; do
        read -r -p "paste a public key (blank to finish): " key </dev/tty
        [ -n "$key" ] || break
        printf '%s\n' "$key" >"$tmp"
        if ! ssh-keygen -l -f "$tmp" >/dev/null 2>&1; then
            echo "not a public key, ignored" >&2
            continue
        fi
        printf '%s\n' "$key" >>/root/.ssh/authorized_keys
        log "added $(ssh-keygen -l -f "$tmp")"
    done
    rm -f "$tmp"
    chmod 700 /root/.ssh
    chmod 600 /root/.ssh/authorized_keys
}

# Root by key only. Never PermitRootLogin no: the web UI shell, migration and
# clustering log in as root over SSH.
check_ssh_harden() { [ -f /etc/ssh/sshd_config.d/99-local.conf ]; }
default_ssh_harden() { echo yes; }
do_ssh_harden() {
    if [ ! -s /root/.ssh/authorized_keys ]; then
        echo "refusing: /root/.ssh/authorized_keys is empty, this would lock you out - run ssh-keys first" >&2
        return 1
    fi
    cat >/etc/ssh/sshd_config.d/99-local.conf <<'EOF'
PermitRootLogin prohibit-password
PasswordAuthentication no
EOF
    sshd -t || { rm -f /etc/ssh/sshd_config.d/99-local.conf; return 1; }
    systemctl reload ssh
    log "verify a new key-based session before closing this one"
}

check_tailscale() { tailscale status >/dev/null 2>&1; }
default_tailscale() { echo yes; }
do_tailscale() {
    curl -fsSL https://tailscale.com/install.sh | sh
    tailscale up  # prints a URL to open
}

# Advertised routes live in the prefs; `tailscale status --json` only lists a
# route once it has been approved in the admin console.
check_subnet_router() {
    [ -f /etc/sysctl.d/99-tailscale.conf ] &&
        tailscale debug prefs 2>/dev/null | grep -qF "\"$LAN_ROUTE\""
}
default_subnet_router() { echo no; }
do_subnet_router() {
    if ! have tailscale; then
        echo "tailscale is not installed - run the tailscale step first" >&2
        return 1
    fi
    cat >/etc/sysctl.d/99-tailscale.conf <<'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOF
    sysctl -p /etc/sysctl.d/99-tailscale.conf
    # `tailscale set` changes one pref and keeps the rest; older clients only
    # have `up`, which wants every non-default flag repeated.
    if tailscale set --help >/dev/null 2>&1; then
        tailscale set --advertise-routes="$LAN_ROUTE"
    else
        tailscale up --advertise-routes="$LAN_ROUTE"
    fi
    log "approve $LAN_ROUTE in the Tailscale admin console: Machines -> $(hostname) -> Edit route settings"
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
