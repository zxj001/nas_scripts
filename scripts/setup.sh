#!/usr/bin/env bash
# setup-machine - set up a Debian 13 or macOS box the way docs/ describes.
#
#   curl -fsSL https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/setup.sh | bash
#
# Everything below is a definition; the last line calls main, so a half
# downloaded copy does nothing.
set -euo pipefail

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
STEPS=(dev-tools firstmate)

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

check_dev_tools() { have git && have jq && have rg; }
default_dev_tools() { echo yes; }
do_dev_tools() {
    case "$OS" in
        debian) sudo apt-get update && sudo apt-get install -y git curl ca-certificates build-essential jq ripgrep ;;
        macos) brew install jq ripgrep ;;
    esac
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
                    echo "--only needs a step list, e.g. --only dev-tools,firstmate" >&2
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

main() {
    parse_args "$@"
    if [ "$OPT_HELP" = 1 ]; then
        usage
        return 0
    fi
    detect_os
    self_update "$@"
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

    for step in "${todo[@]}"; do
        if [ "$OPT_YES" != 1 ] && ! prompt "$step" "$("$(fname default "$step")")"; then
            continue
        fi
        log "$step"
        set +e
        (
            set -e
            "$(fname "do" "$step")"
        )
        rc=$?
        set -e
        if [ "$rc" -ne 0 ]; then
            echo "step failed: $step" >&2
            return 1
        fi
    done
    log "done. Sign in where needed: gh auth login, codex, pi /login, claude"
}

main "$@"
