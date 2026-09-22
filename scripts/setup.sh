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
FIRSTMATE_DIR="$HOME/firstmate"

# Ordered step registry. Every name here needs three functions, with "-"
# replaced by "_":
#
#   check_<name>    0 = done, 1 = todo, 2 = not applicable, 3 = manual setup
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
STEPS=(brew sudo upgrade guest-agent no-sleep power-restore ssh ssh-keys ssh-harden tailscale dev-tools gh node codex pi claude herdr firstmate shellfish gpu)

usage() {
    cat <<'EOF'
Usage: setup-machine [--status] [--yes] [--only a,b] [--firstmate-dir PATH] [--help]

  --status     run the checks, print the table and exit
  --yes        run every not-done step without prompting
  --only a,b   only these steps, comma separated
  --firstmate-dir PATH
               FirstMate checkout location (default: ~/firstmate)
  --help       this text
EOF
}

log() { printf '==> %s\n' "$*"; }

have() { command -v "$1" >/dev/null 2>&1; }

append_once() {
    local line="$1" file="$2"
    if ! grep -qxF "$line" "$file" 2>/dev/null; then
        # A leading newline also handles a profile without a trailing newline.
        printf '\n%s\n' "$line" >>"$file"
    fi
}

# Publish only complete files, without replacing an existing file or symlink.
# The hard link is atomic and fails if another writer claimed the destination.
# Existing identical regular files are left untouched.
create_config() {
    local source="$1" target="$2" stage
    CONFIG_CREATED=0
    if sudo test -e "$target" || sudo test -L "$target"; then
        if ! sudo test -L "$target" && sudo test -f "$target" && sudo cmp -s "$source" "$target"; then
            return 0
        fi
        echo "refusing to replace existing configuration: $target" >&2
        return 1
    fi
    stage="$(sudo mktemp "${target}.XXXXXX")" || return 1
    if ! sudo install -m 644 "$source" "$stage" || ! sudo ln -T "$stage" "$target"; then
        sudo rm -f "$stage"
        return 1
    fi
    sudo rm -f "$stage"
    CONFIG_CREATED=1
}

ensure_https() {
    if [ "$OS" = debian ] && { ! have curl ||
        [ "$(dpkg-query -W -f='${Status}' ca-certificates 2>/dev/null)" != 'install ok installed' ]; }; then
        sudo apt-get update
        sudo apt-get install -y curl ca-certificates
    fi
}

# Do not execute a truncated download if curl fails halfway through.
run_installer() {
    local url="$1" interpreter="$2" script rc=0
    ensure_https
    script="$(mktemp)" || return 1
    curl -fsSL "$url" -o "$script" || rc=$?
    if [ "$rc" = 0 ]; then
        "$interpreter" "$script" || rc=$?
    fi
    rm -f "$script"
    return "$rc"
}

# check/do/default function name for a step ("ssh-keys" -> "check_ssh_keys")
fname() { printf '%s_%s' "$1" "${2//-/_}"; }

detect_os() {
    case "$(uname -s)" in
        Linux)
            # The apt sources and package names below target Debian 13.
            if [ ! -r /etc/os-release ] || ! (
                # shellcheck source=/dev/null
                . /etc/os-release
                [ "${ID:-}" = debian ] && [ "${VERSION_ID:-}" = 13 ]
            ); then
                echo "unsupported Linux distribution: this script requires Debian 13" >&2
                exit 1
            fi
            OS=debian
            ;;
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
    have sudo && id -nG | grep -qw sudo
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
    sudo apt-get -o Dpkg::Options::=--force-confold full-upgrade --no-remove -y
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
    local target
    for target in sleep suspend hibernate hybrid-sleep; do
        [ "$(systemctl is-enabled "$target.target" 2>/dev/null)" = masked ] || return 1
    done
}
default_no_sleep() { debian_only; }
do_no_sleep() {
    sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
}

# Restoring mains power is a firmware/BMC feature, not an OS boot service.
power_restore_virtual() {
    [ "$OS" = debian ] && have systemd-detect-virt && systemd-detect-virt --quiet
}
local_ipmi() {
    [ -e /dev/ipmi0 ] || [ -e /dev/ipmi/0 ] || [ -e /dev/ipmidev/0 ] ||
        [ -d /sys/class/ipmi/ipmi0 ]
}
mac_power_restore_supported() {
    pmset -g cap 2>/dev/null | grep -Eq '^[[:space:]]*autorestart[[:space:]]*$'
}
mac_power_restore_enabled() {
    pmset -g custom 2>/dev/null | awk '
        $1 == "autorestart" { seen = 1; if ($2 != 1) disabled = 1 }
        END { exit !(seen && !disabled) }
    '
}
ipmi_power_restore_enabled() {
    grep -Eq '^[[:space:]]*Power Restore Policy[[:space:]]*:[[:space:]]*always-on[[:space:]]*$'
}
check_power_restore() {
    if power_restore_virtual; then return 2; fi
    case "$OS" in
        debian)
            local_ipmi || return 3
            have ipmitool || return 1
            sudo -n ipmitool -I open chassis status 2>/dev/null | ipmi_power_restore_enabled
            ;;
        macos)
            mac_power_restore_supported || return 3
            mac_power_restore_enabled
            ;;
    esac
}
default_power_restore() { echo no; }
power_restore_guidance() {
    log "power-restore requires manual firmware setup; it has not been verified"
    log "Desktop/mini PC: BIOS/UEFI -> Power or APM -> Restore on AC Power Loss / AC Recovery -> Power On"
    log "Choose Always On for unattended startup; Last State only restarts a previously running machine"
    log "Mac: System Settings -> Energy -> Start up automatically after a power failure (if available)"
    log "Server: BMC/IPMI power-restore policy; VM: configure the host and the VM's Start at boot setting"
    log "See docs/power-restore.md for vendor settings, UPS behavior, Wake-on-LAN and scheduled-start alternatives"
}
do_power_restore() {
    if power_restore_virtual; then
        power_restore_guidance
        return 0
    fi
    case "$OS" in
        debian)
            if ! local_ipmi; then power_restore_guidance; return 0; fi
            if ! have ipmitool; then
                sudo apt-get update
                sudo apt-get install -y ipmitool
            fi
            sudo modprobe ipmi_devintf
            if ! sudo ipmitool -I open chassis policy always-on; then
                power_restore_guidance
                return 1
            fi
            if ! sudo ipmitool -I open chassis status | ipmi_power_restore_enabled; then
                echo "IPMI power-restore policy was not verified; check the BMC settings" >&2
                return 1
            fi
            ;;
        macos)
            if ! mac_power_restore_supported; then power_restore_guidance; return 0; fi
            sudo pmset -a autorestart 1
            if ! mac_power_restore_enabled; then
                echo "automatic restart was not verified; check System Settings -> Energy" >&2
                return 1
            fi
            ;;
    esac
    log "automatic restart after a power failure is enabled; no reboot or power-off was performed"
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
        if grep -qxF "$key" "$HOME/.ssh/authorized_keys"; then
            log "key already present"
            continue
        fi
        printf '\n%s\n' "$key" >>"$HOME/.ssh/authorized_keys"
        log "added $(ssh-keygen -l -f "$tmp")"
    done
    rm -f "$tmp"
    chmod 700 "$HOME/.ssh"
    chmod 600 "$HOME/.ssh/authorized_keys"
}

check_ssh_harden() {
    [ "$OS" = debian ] || return 2
    # Read effective settings, including other drop-ins. Never prompt in --status.
    sudo -n /usr/sbin/sshd -T 2>/dev/null | ssh_hardening_policy_ok
}
ssh_hardening_policy_ok() {
    local policy
    policy="$(cat)"
    grep -qx 'permitrootlogin no' <<<"$policy" &&
        grep -qx 'pubkeyauthentication yes' <<<"$policy" &&
        grep -qx 'passwordauthentication no' <<<"$policy"
}
ssh_hardening_config() {
    printf '%s\n' 'PermitRootLogin no' 'PubkeyAuthentication yes' 'PasswordAuthentication no'
}
default_ssh_harden() { debian_only; }
do_ssh_harden() {
    local config=/etc/ssh/sshd_config.d/99-local.conf tmp
    if sudo test -e "$config" || sudo test -L "$config"; then
        # A previous run or the operator already created this file. Accept
        # equivalent policy regardless of comments/format; never replace it.
        if sudo /usr/sbin/sshd -t && sudo /usr/sbin/sshd -T | ssh_hardening_policy_ok; then
            sudo systemctl reload ssh
            log "existing SSH policy is configured; continuing"
        else
            log "SSH hardening skipped: preserving $config; effective settings still need review"
            log "check with: sudo /usr/sbin/sshd -T; continuing the remaining setup steps"
        fi
        return 0
    fi
    if [ "$(id -u)" = 0 ] || ! ssh-keygen -l -f "$HOME/.ssh/authorized_keys" >/dev/null 2>&1; then
        echo "refusing: hardening requires a non-root user with a valid authorized key - run ssh-keys first" >&2
        return 1
    fi
    tmp="$(mktemp)"
    ssh_hardening_config >"$tmp"
    sudo mkdir -p /etc/ssh/sshd_config.d
    if ! create_config "$tmp" "$config"; then rm -f "$tmp"; return 1; fi
    rm -f "$tmp"
    if ! sudo /usr/sbin/sshd -t ||
        ! sudo /usr/sbin/sshd -T | ssh_hardening_policy_ok || ! sudo systemctl reload ssh; then
        if [ "$CONFIG_CREATED" = 1 ]; then
            sudo rm -f "$config"
            sudo systemctl reload ssh || true
        fi
        echo "SSH validation/reload failed; check earlier Include settings before retrying" >&2
        return 1
    fi
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
    run_installer https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh /bin/bash </dev/tty
    if [ -x /opt/homebrew/bin/brew ]; then brew=/opt/homebrew/bin/brew; else brew=/usr/local/bin/brew; fi
    [ -x "$brew" ]
    append_once "eval \"\$($brew shellenv)\"" "$HOME/.zprofile"
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
            if ! have tailscale; then
                run_installer https://tailscale.com/install.sh sh
            fi
            sudo tailscale up  # prints a URL to open
            ;;
        macos)
            if ! have tailscale && [ ! -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]; then
                brew install --cask tailscale
            fi
            log "open the Tailscale app and sign in, then rerun --status"
            ;;
    esac
}

check_dev_tools() {
    if [ "$OS" = debian ]; then
        have curl && have make && have gcc && have g++ &&
            [ "$(dpkg-query -W -f='${Status}' ca-certificates 2>/dev/null)" = 'install ok installed' ] || return 1
    fi
    have git && have jq && have rg && python_ok &&
        [ -d "$HOME/projects" ] && [ -d "$HOME/tools" ]
}
# pip, plus venv: Debian's python3 refuses system-wide pip installs (PEP 668),
# so pip is only usable inside a venv, and python3-venv brings ensurepip.
python_ok() {
    have python3 && python3 -m pip --version >/dev/null 2>&1 || return 1
    [ "$OS" != debian ] || python3 -c 'import ensurepip, venv' 2>/dev/null
}
default_dev_tools() { echo yes; }
do_dev_tools() {
    case "$OS" in
        debian)
            sudo apt-get update
            sudo apt-get install -y git curl ca-certificates build-essential jq ripgrep \
                python3 python3-pip python3-venv
            ;;
        macos) brew install jq ripgrep python ;;  # git comes with the Xcode CLT
    esac
    mkdir -p "$HOME/projects" "$HOME/tools"
}

check_gh() { have gh; }
default_gh() { echo yes; }
do_gh() {
    case "$OS" in
        debian)
            # GitHub's apt repo, verbatim from docs/05-dev-tools.md.
            local keyring=/etc/apt/keyrings/githubcli-archive-keyring.gpg tmp
            ensure_https
            sudo mkdir -p -m 755 /etc/apt/keyrings
            tmp="$(mktemp)"
            if ! sudo test -e "$keyring" && ! sudo test -L "$keyring"; then
                if ! curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o "$tmp" ||
                    ! create_config "$tmp" "$keyring"; then rm -f "$tmp"; return 1; fi
            fi
            sudo mkdir -p -m 755 /etc/apt/sources.list.d
            echo "deb [arch=$(dpkg --print-architecture) signed-by=$keyring] https://cli.github.com/packages stable main" >"$tmp"
            if ! create_config "$tmp" /etc/apt/sources.list.d/github-cli.list; then rm -f "$tmp"; return 1; fi
            rm -f "$tmp"
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
    # Source nvm here too, so the pi step below sees npm in this same run.
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    if [ ! -s "$NVM_DIR/nvm.sh" ]; then
        if [ -e "$NVM_DIR" ] || [ -L "$NVM_DIR" ]; then
            echo "refusing to install over an existing incomplete nvm directory: $NVM_DIR" >&2
            return 1
        fi
        mkdir -p "$NVM_DIR"
        run_installer https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.7/install.sh bash
    fi
    [ -s "$NVM_DIR/nvm.sh" ] || return 1
    # shellcheck source=/dev/null
    . "$NVM_DIR/nvm.sh"
    nvm install 22
    if [ ! -e "$NVM_DIR/alias/default" ] && [ ! -L "$NVM_DIR/alias/default" ]; then
        nvm alias default 22
    fi
}

check_codex() { have codex; }
default_codex() { echo yes; }
do_codex() {
    case "$OS" in
        debian) run_installer https://chatgpt.com/codex/install.sh sh ;;
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
            run_installer https://claude.ai/install.sh bash
            # shellcheck disable=SC2016  # literal, expanded when .bashrc runs
            append_once 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.bashrc"
            ;;
        macos) brew install --cask claude-code ;;
    esac
}

check_herdr() { have herdr; }
default_herdr() { echo yes; }
do_herdr() {
    run_installer https://herdr.dev/install.sh sh
    if [ "$OS" = macos ]; then
        # shellcheck disable=SC2016  # literal, expanded when .zprofile runs
        append_once 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.zprofile"
    fi
    if have claude; then
        if [ -e "$HOME/.claude/settings.json" ] || [ -L "$HOME/.claude/settings.json" ]; then
            log "preserving existing Claude settings; run 'herdr integration install claude' to configure hooks yourself"
        else
            herdr integration install claude
        fi
    fi
}

check_firstmate() { [ -e "$FIRSTMATE_DIR/.git" ]; }
default_firstmate() { echo yes; }
do_firstmate() {
    if check_firstmate; then return 0; fi
    if [ -e "$FIRSTMATE_DIR" ] || [ -L "$FIRSTMATE_DIR" ]; then
        echo "refusing to clone over existing path: $FIRSTMATE_DIR" >&2
        return 1
    fi
    mkdir -p "$(dirname "$FIRSTMATE_DIR")"
    git clone https://github.com/kunchenguid/firstmate "$FIRSTMATE_DIR"
}

# ShellFish widget (docs/08-shellfish-widgets.md): cron pushes CPU, CPU temp,
# memory and disk usage to the iPhone. The app writes ~/.shellfishrc; until it
# has, there is nothing here but guidance. Its widget function needs openssl,
# xxd and curl: without xxd it still exits 0 but pushes a payload the phone
# cannot decrypt. The stats come from /proc, so this is Debian only.
SHELLFISH_WIDGET="scripts/shellfish_widget.sh"
shellfish_tools() { have openssl && have xxd && have curl && have crontab; }
shellfish_cron_line() {
    printf '*/15 * * * * %q >/dev/null 2>&1\n' "$REPO_DIR/$SHELLFISH_WIDGET"
}
check_shellfish() {
    [ "$OS" = debian ] || return 2
    [ -e "$HOME/.shellfishrc" ] || return 3
    shellfish_tools || return 1
    crontab -l 2>/dev/null | grep -qF "$SHELLFISH_WIDGET"
}
default_shellfish() { debian_only; }
do_shellfish() {
    local current
    if [ ! -e "$HOME/.shellfishrc" ]; then
        log "in ShellFish on the iPhone: this server's settings -> Install Shell Integration, then rerun"
        log "see docs/08-shellfish-widgets.md"
        return 0
    fi
    if ! shellfish_tools; then
        sudo apt-get update
        sudo apt-get install -y openssl xxd curl cron
    fi
    if [ ! -x "$REPO_DIR/$SHELLFISH_WIDGET" ]; then
        echo "missing $REPO_DIR/$SHELLFISH_WIDGET - rerun after the repo is cloned" >&2
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
    if ! grep -qF "$SHELLFISH_WIDGET" <<<"$current"; then
        printf '%s%s\n' "${current:+$current$'\n'}" "$(shellfish_cron_line)" | crontab -
    fi
    "$REPO_DIR/$SHELLFISH_WIDGET"
    log "widget sent; add a ShellFish widget on the iPhone Home Screen if you have not"
}

# An NVIDIA card, if there is one. Debian installs the driver; macOS has none,
# so the card is reported and nothing is offered. Passthrough itself is a
# Proxmox host job - docs/gpu-passthrough.md.
nvidia_name() {
    case "$OS" in
        debian) lspci -nn 2>/dev/null | grep -iE 'vga|3d' | grep -i nvidia ;;
        macos) system_profiler SPDisplaysDataType 2>/dev/null | grep -i nvidia ;;
    esac
}

check_gpu() {
    local card
    card="$(nvidia_name | head -1)"
    if [ -z "$card" ]; then
        if [ "$OS" = debian ] && [ "$(systemd-detect-virt 2>/dev/null)" = kvm ]; then
            echo "gpu: no NVIDIA device in this VM - pass the card through first, see docs/gpu-passthrough.md" >&2
        fi
        return 2
    fi
    # On stderr so the status table on stdout stays two columns.
    echo "gpu: $card" >&2
    # macOS ships no NVIDIA driver, so a card found there is all there is to do.
    [ "$OS" = debian ] || return 0
    nvidia-smi >/dev/null 2>&1
}
default_gpu() { debian_only; }
do_gpu() {
    if have mokutil && mokutil --sb-state 2>/dev/null | grep -q 'SecureBoot enabled'; then
        echo "gpu: Secure Boot is on - the DKMS-built nvidia module will not load until Secure Boot is disabled or the dkms key is enrolled, see docs/gpu-passthrough.md" >&2
    fi
    # Drop in only the components no active source already carries, so apt
    # never sees a target configured twice. Include our existing drop-in on
    # reruns; a component after a # (the netinst
    # cdrom line mentions contrib) does not count. -q exits 0 on a match even
    # when a listed file is missing.
    local c tmp missing=""
    for c in contrib non-free non-free-firmware; do
        grep -rqsE "^[^#]*(^|[[:space:]])$c([[:space:]]|$)" \
            /etc/apt/sources.list /etc/apt/sources.list.d/ || missing="$missing $c"
    done
    if [ -n "$missing" ]; then
        tmp="$(mktemp)"
        cat >"$tmp" <<EOF
Types: deb
URIs: http://deb.debian.org/debian
Suites: trixie trixie-updates
Components:$missing

Types: deb
URIs: http://security.debian.org/debian-security
Suites: trixie-security
Components:$missing
EOF
        if ! create_config "$tmp" /etc/apt/sources.list.d/nonfree.sources; then rm -f "$tmp"; return 1; fi
        rm -f "$tmp"
    fi
    sudo apt-get update
    sudo apt-get install -y nvidia-driver firmware-misc-nonfree
    log "reboot to load the driver, then check with nvidia-smi"
}

# --- runner -----------------------------------------------------------------

# Clone the repo if it is missing and link it onto PATH as setup-machine.
install_repo() {
    if [ ! -e "$REPO_DIR/.git" ]; then
        if [ -e "$REPO_DIR" ] || [ -L "$REPO_DIR" ]; then
            echo "refusing to clone over existing path: $REPO_DIR" >&2
            return 1
        fi
        log "cloning $REPO_URL into $REPO_DIR"
        mkdir -p "$(dirname "$REPO_DIR")"
        git clone "$REPO_URL" "$REPO_DIR"
    fi
    mkdir -p "$HOME/.local/bin"
    local launcher="$HOME/.local/bin/setup-machine"
    if [ -L "$launcher" ] && [ "$(readlink "$launcher")" = "$REPO_DIR/scripts/setup.sh" ]; then
        return 0
    fi
    if [ -e "$launcher" ] || [ -L "$launcher" ]; then
        echo "preserving existing launcher: $launcher" >&2
        return 0
    fi
    ln -s "$REPO_DIR/scripts/setup.sh" "$launcher"
}

# Fast-forward or clone the repo, link it and re-exec the fresh copy.
# SETUP_UPDATED stops that from looping. Skipped when git is missing, which is
# the first run on a bare box - the runner installs the repo once a step
# (dev-tools) has brought git.
self_update() {
    if [ -n "${SETUP_UPDATED:-}" ]; then
        return 0
    fi
    if ! have git; then
        log "git not installed yet, skipping self-update"
        return 0
    fi
    if [ -e "$REPO_DIR/.git" ]; then
        local origin branch dirty
        origin="$(git -C "$REPO_DIR" remote get-url origin)" || return 1
        branch="$(git -C "$REPO_DIR" symbolic-ref --short -q HEAD)" || branch=""
        dirty="$(git -C "$REPO_DIR" status --porcelain)" || return 1
        if { [ "$origin" != "$REPO_URL" ] && [ "$origin" != "$REPO_URL.git" ]; } ||
            [ "$branch" != main ] || [ -n "$dirty" ]; then
            log "preserving existing checkout (different origin, branch, or local changes); skipping self-update"
            return 0
        fi
        log "updating $REPO_DIR"
        git -C "$REPO_DIR" pull --ff-only
    fi
    install_repo
    SETUP_UPDATED=1 exec bash "$REPO_DIR/scripts/setup.sh" "$@"
}

# A non-login shell (ssh host 'bash -s', cron) has neither ~/.local/bin nor
# nvm, so the checks would call installed tools todo.
tool_path() {
    if [ "$OS" = macos ] && ! have brew; then
        if [ -x /opt/homebrew/bin/brew ]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [ -x /usr/local/bin/brew ]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi
    fi
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) export PATH="$HOME/.local/bin:$PATH" ;;
    esac
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    # shellcheck source=/dev/null
    if [ -s "$NVM_DIR/nvm.sh" ]; then . "$NVM_DIR/nvm.sh"; fi
}

parse_args() {
    OPT_STATUS=0
    OPT_YES=0
    OPT_HELP=0
    OPT_ONLY=""
    FIRSTMATE_DIR="$HOME/firstmate"
    while [ $# -gt 0 ]; do
        case "$1" in
            --status) OPT_STATUS=1 ;;
            --yes) OPT_YES=1 ;;
            --firstmate-dir)
                if [ $# -lt 2 ] || [ -z "$2" ] || [[ "$2" == --* ]]; then
                    echo "--firstmate-dir needs a checkout path" >&2
                    exit 2
                fi
                # Normalize relative paths so git cannot treat them as options.
                case "$2" in
                    /*) FIRSTMATE_DIR="$2" ;;
                    *) FIRSTMATE_DIR="$PWD/$2" ;;
                esac
                shift
                ;;
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
    select_steps
    if [ "$OPT_STATUS" != 1 ]; then self_update "$@"; fi
    tool_path

    local step rc status ran=""
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
        # Earlier steps can make later ones complete (or expose existing tools).
        rc=0
        "$(fname check "$step")" || rc=$?
        if [ "$rc" = 0 ] || [ "$rc" = 2 ]; then continue; fi
        if [ "$OPT_YES" != 1 ] && ! prompt "$step" "$("$(fname default "$step")")"; then
            continue
        fi
        log "$step"
        "$(fname "do" "$step")"
        ran="$ran $step"
        # First run on a bare box: git just arrived, so clone and link now.
        if [ -z "${SETUP_UPDATED:-}" ] && [ ! -d "$REPO_DIR/.git" ] && have git; then
            install_repo
        fi
    done
    signin_list
    reboot_reminder "$ran"
}

main "$@"
