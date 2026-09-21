#!/usr/bin/env bash
# setup-machine - set up a Debian 13 or macOS box the way docs/ describes.
#
#   curl -fsSL https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/setup.sh | bash
#
# Everything below is a definition; the last line calls main, so a half
# downloaded copy does nothing.
set -Eeuo pipefail

REPO_URL="https://github.com/zxj001/nas_scripts"
REPO_DIR="$HOME/tools/nas_scripts"

# Ordered step registry. Every name here needs three functions, with "-"
# replaced by "_":
#
#   check_<name>    0 = done, 1 = todo, 2 = not applicable on this OS
#   do_<name>       make it so
#   default_<name>  echo yes|no - the prompt default for $OS
#
# Adding a step is adding its name here plus those three functions, nothing
# else. tests/test_setup.sh fails if a registered step is missing one.
#
# A do_ that returns 75 means "stop here, rerun later" - the runner stops
# without an error. sudo needs it: a new group only applies to a new login.
#
# brew comes first: every other macOS step installs through it. sudo comes
# next: every other Debian step shells out to sudo.
STEPS=(brew sudo upgrade guest-agent no-sleep ssh ssh-keys ssh-harden tailscale dev-tools gh node codex pi claude herdr firstmate)

usage() {
    cat <<'EOF'
Usage: setup-machine [--status] [--yes] [--only a,b] [--help]

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

detect_os() {
    case "$(uname -s)" in
        Linux) OS=debian ;;
        Darwin) OS=macos ;;
        *)
            echo "unsupported OS: $(uname -s) - this script does Debian 13 and macOS" >&2
            exit 1
            ;;
    esac
}

# --- steps ------------------------------------------------------------------

# The Debian server steps (docs/01-docs/03) are n/a anywhere else.
debian_only() { if [ "$OS" = debian ]; then echo yes; else echo no; fi; }

check_sudo() {
    [ "$OS" = debian ] || return 2
    id -nG | grep -qw sudo
}
default_sudo() { debian_only; }
do_sudo() {
    log "asking for the root password to add $(id -un) to the sudo group"
    su -c "apt-get update && apt-get install -y sudo && /usr/sbin/usermod -aG sudo $(id -un)" </dev/tty
    echo "log out, log back in, then rerun setup-machine"
    return 75
}

# "Always offered" in practice means: offered whenever apt has something to
# install. Reporting todo on an up-to-date box would make --status lie.
# apt-get -s needs no root and reads the package lists as they are, so a box
# that hasn't run `apt update` in a while may under-report.
check_upgrade() {
    [ "$OS" = debian ] || return 2
    if apt-get -s full-upgrade 2>/dev/null | grep '^Inst ' >/dev/null; then
        return 1
    fi
    return 0
}
default_upgrade() { debian_only; }
do_upgrade() {
    sudo apt-get update
    sudo apt-get full-upgrade -y
}

check_guest_agent() {
    [ "$OS" = debian ] || return 2
    [ "$(systemd-detect-virt 2>/dev/null)" = kvm ] || return 2
    systemctl is-enabled --quiet qemu-guest-agent 2>/dev/null
}
default_guest_agent() { debian_only; }
do_guest_agent() {
    sudo apt-get install -y qemu-guest-agent spice-vdagent
    sudo systemctl enable --now qemu-guest-agent
    log "in Proxmox: VM -> Options -> QEMU Guest Agent -> Enabled, then reboot the VM"
}

check_no_sleep() {
    [ "$OS" = debian ] || return 2
    [ "$(systemctl is-enabled sleep.target 2>/dev/null)" = masked ]
}
default_no_sleep() { debian_only; }
do_no_sleep() {
    sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
}

check_ssh() {
    [ "$OS" = debian ] || return 2
    systemctl is-active --quiet ssh 2>/dev/null
}
default_ssh() { debian_only; }
do_ssh() {
    sudo apt-get install -y openssh-server
    sudo systemctl enable --now ssh
    log "reachable at: $(hostname -I)"
}

check_ssh_keys() {
    [ "$OS" = debian ] || return 2
    [ -s "$HOME/.ssh/authorized_keys" ]
}
default_ssh_keys() { debian_only; }
do_ssh_keys() {
    local key tmp
    mkdir -p "$HOME/.ssh"
    touch "$HOME/.ssh/authorized_keys"
    tmp="$(mktemp)"
    while :; do
        read -r -p "paste a public key (blank to finish): " key </dev/tty
        [ -n "$key" ] || break
        printf '%s\n' "$key" >"$tmp"
        if ! ssh-keygen -l -f "$tmp" >/dev/null 2>&1; then
            echo "not a public key, ignored" >&2
            continue
        fi
        printf '%s\n' "$key" >>"$HOME/.ssh/authorized_keys"
        log "added $(ssh-keygen -l -f "$tmp")"
    done
    rm -f "$tmp"
    chmod 700 "$HOME/.ssh"
    chmod 600 "$HOME/.ssh/authorized_keys"
}

check_ssh_harden() {
    [ "$OS" = debian ] || return 2
    [ -f /etc/ssh/sshd_config.d/99-local.conf ]
}
default_ssh_harden() { debian_only; }
do_ssh_harden() {
    if [ ! -s "$HOME/.ssh/authorized_keys" ]; then
        echo "refusing: $HOME/.ssh/authorized_keys is empty, this would lock you out - run ssh-keys first" >&2
        return 1
    fi
    sudo tee /etc/ssh/sshd_config.d/99-local.conf >/dev/null <<'EOF'
PermitRootLogin no
PubkeyAuthentication yes
PasswordAuthentication no
EOF
    sudo sshd -t || { sudo rm -f /etc/ssh/sshd_config.d/99-local.conf; return 1; }
    sudo systemctl reload ssh
    log "verify a new key-based session before closing this one"
}

# brew is macOS only and comes first: every other macOS step installs through it.
check_brew() {
    [ "$OS" = macos ] || return 2
    have brew
}
default_brew() { echo yes; }
do_brew() {
    local brew
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" </dev/tty
    if [ -x /opt/homebrew/bin/brew ]; then brew=/opt/homebrew/bin/brew; else brew=/usr/local/bin/brew; fi
    [ -x "$brew" ]
    echo "eval \"\$($brew shellenv)\"" >>"$HOME/.zprofile"
    eval "$("$brew" shellenv)"
}

check_tailscale() {
    local ts=tailscale
    if [ "$OS" = macos ] && ! have tailscale; then
        ts=/Applications/Tailscale.app/Contents/MacOS/Tailscale
    fi
    "$ts" status >/dev/null 2>&1
}
default_tailscale() { echo yes; }
do_tailscale() {
    case "$OS" in
        debian)
            curl -fsSL https://tailscale.com/install.sh | sh
            sudo tailscale up  # prints a URL to open
            ;;
        macos)
            brew install --cask tailscale
            log "open the Tailscale app and sign in, then rerun --status"
            ;;
    esac
}

check_dev_tools() {
    have git && have jq && have rg &&
        [ -d "$HOME/projects" ] && [ -d "$HOME/tools" ]
}
default_dev_tools() { echo yes; }
do_dev_tools() {
    case "$OS" in
        debian)
            sudo apt-get update
            sudo apt-get install -y git curl ca-certificates build-essential jq ripgrep
            ;;
        macos) brew install jq ripgrep ;;  # git comes with the Xcode CLT
    esac
    mkdir -p "$HOME/projects" "$HOME/tools"
}

check_gh() { have gh; }
default_gh() { echo yes; }
do_gh() {
    case "$OS" in
        debian)
            # GitHub's apt repo, verbatim from docs/05-dev-tools.md.
            local keyring=/etc/apt/keyrings/githubcli-archive-keyring.gpg
            sudo apt-get update
            sudo apt-get install -y wget
            sudo mkdir -p -m 755 /etc/apt/keyrings
            wget -nv -O- https://cli.github.com/packages/githubcli-archive-keyring.gpg |
                sudo tee "$keyring" >/dev/null
            sudo chmod go+r "$keyring"
            sudo mkdir -p -m 755 /etc/apt/sources.list.d
            echo "deb [arch=$(dpkg --print-architecture) signed-by=$keyring] https://cli.github.com/packages stable main" |
                sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
            sudo apt-get update
            sudo apt-get install -y gh
            ;;
        macos) brew install gh ;;
    esac
}

# Pi needs Node 22.19.0 or newer, so a too-old node counts as todo.
check_node() {
    local v
    have node || return 1
    v="$(node -v)"
    [ "$(printf '22.19.0\n%s\n' "${v#v}" | sort -V | head -n1)" = 22.19.0 ]
}
default_node() { echo yes; }
do_node() {
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.7/install.sh | bash
    # Source nvm here too, so the pi step below sees npm in this same run.
    export NVM_DIR="$HOME/.nvm"
    # shellcheck source=/dev/null
    [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
    nvm install 22
    nvm alias default 22
}

check_codex() { have codex; }
default_codex() { echo yes; }
do_codex() {
    case "$OS" in
        debian) curl -fsSL https://chatgpt.com/codex/install.sh | sh ;;
        macos) brew install --cask codex ;;
    esac
}

check_pi() { have pi; }
default_pi() { echo yes; }
do_pi() { npm install -g --ignore-scripts @earendil-works/pi-coding-agent; }

check_claude() { have claude; }
default_claude() { echo yes; }
do_claude() {
    case "$OS" in
        debian)
            curl -fsSL https://claude.ai/install.sh | bash
            case ":$PATH:" in
                *":$HOME/.local/bin:"*) ;;
                *)
                    # shellcheck disable=SC2016  # literal, expanded when .bashrc runs
                    echo 'export PATH="$HOME/.local/bin:$PATH"' >>"$HOME/.bashrc"
                    export PATH="$HOME/.local/bin:$PATH"
                    ;;
            esac
            ;;
        macos) brew install --cask claude-code ;;
    esac
}

check_herdr() { have herdr; }
default_herdr() { echo yes; }
do_herdr() {
    curl -fsSL https://herdr.dev/install.sh | sh
    if [ "$OS" = macos ]; then
        case ":$PATH:" in
            *":$HOME/.local/bin:"*) ;;
            *)
                # shellcheck disable=SC2016  # literal, expanded when .zprofile runs
                echo 'export PATH="$HOME/.local/bin:$PATH"' >>"$HOME/.zprofile"
                export PATH="$HOME/.local/bin:$PATH"
                ;;
        esac
    fi
    if have claude; then
        herdr integration install claude
    fi
}

check_firstmate() { [ -d "$HOME/tools/firstmate/.git" ]; }
default_firstmate() { echo yes; }
do_firstmate() {
    mkdir -p "$HOME/tools"
    git clone https://github.com/kunchenguid/firstmate "$HOME/tools/firstmate"
}

# --- runner -----------------------------------------------------------------

# Clone or fast-forward the repo, link it onto PATH and re-exec the fresh copy.
# SETUP_UPDATED stops that from looping. Skipped when git is missing, which is
# the first run on a bare box - dev-tools installs it.
self_update() {
    if [ -n "${SETUP_UPDATED:-}" ]; then
        return 0
    fi
    if ! have git; then
        log "git not installed yet, skipping self-update"
        return 0
    fi
    if [ -d "$REPO_DIR/.git" ]; then
        log "updating $REPO_DIR"
        git -C "$REPO_DIR" pull --ff-only
    else
        log "cloning $REPO_URL into $REPO_DIR"
        mkdir -p "$(dirname "$REPO_DIR")"
        git clone "$REPO_URL" "$REPO_DIR"
    fi
    mkdir -p "$HOME/.local/bin"
    ln -sf "$REPO_DIR/scripts/setup.sh" "$HOME/.local/bin/setup-machine"
    SETUP_UPDATED=1 exec bash "$REPO_DIR/scripts/setup.sh" "$@"
}

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
                    echo "--only needs a step list, e.g. --only node,pi" >&2
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

# $1 is the space separated list of steps that ran this run.
reboot_reminder() {
    if [ -e /run/reboot-required ] || [[ " $1 " == *" upgrade "* ]]; then
        log "reboot to finish: sudo reboot"
    fi
}

# Nothing below can be automated: each one opens its own TUI or browser flow.
signin_list() {
    log "done. Sign in where needed:"
    echo "  gh auth login"
    echo "  codex          # Sign in with ChatGPT"
    echo "  pi             # then /login"
    echo "  claude"
    if [ "$OS" = macos ]; then
        echo "  Tailscale.app  # open it and sign in"
    fi
}

main() {
    parse_args "$@"
    if [ "$OPT_HELP" = 1 ]; then
        usage
        return 0
    fi
    detect_os
    self_update "$@"
    select_steps

    local step rc status ran=""
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

    if [ "$OPT_STATUS" = 1 ]; then
        return 0
    fi
    if [ "${#todo[@]}" -eq 0 ]; then
        reboot_reminder "$ran"
        return 0
    fi
    if [ "$OPT_YES" != 1 ] && ! { : </dev/tty; } 2>/dev/null; then
        echo "no terminal to prompt on - rerun with --yes" >&2
        return 1
    fi

    # Steps run in this shell so what one exports (nvm, brew shellenv, PATH)
    # reaches the next. errexit still applies inside a step; this trap turns a
    # failure into "step failed" and a return of 75 into a clean stop.
    # set -E makes command substitutions inherit this trap but not errexit, so
    # guard on BASH_SUBSHELL: only a failure in the main shell is a step failure.
    trap 'rc=$?; if [ "$BASH_SUBSHELL" != 0 ]; then :; elif [ "$rc" = 75 ]; then reboot_reminder "$ran"; exit 0; else echo "step failed: $step" >&2; reboot_reminder "$ran"; exit 1; fi' ERR
    for step in "${todo[@]}"; do
        if [ "$OPT_YES" != 1 ] && ! prompt "$step" "$("$(fname default "$step")")"; then
            continue
        fi
        log "$step"
        "$(fname "do" "$step")"
        ran="$ran $step"
    done
    signin_list
    reboot_reminder "$ran"
}

main "$@"
