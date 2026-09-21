#!/usr/bin/env bash
# Checks on scripts/setup.sh and scripts/proxmox_setup.sh. Run from anywhere: tests/test_setup.sh
set -euo pipefail

# Never let anything below reach self_update: no clone, pull or exec.
export SETUP_UPDATED=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP="$ROOT/scripts/setup.sh"
fail() {
    echo "FAIL: $*" >&2
    exit 1
}

PVE_SETUP="$ROOT/scripts/proxmox_setup.sh"

if command -v shellcheck >/dev/null 2>&1; then
    shellcheck "$SETUP" "$PVE_SETUP" "${BASH_SOURCE[0]}" || fail "shellcheck"
else
    echo "shellcheck not installed, skipped"
fi

# Every registered step needs its check_/do_/default_ trio. Load the script's
# definitions (everything but the `main "$@"` call) and ask bash for the
# functions it actually defined.
check_registry() {
    local script="$1" step prefix
    bash -n "$script" || fail "syntax error in $script"
    grep -q '^main "\$@"$' "$script" || fail "no main \"\$@\" call found in $script"
    STEPS=()
    eval "$(grep -v '^main "\$@"$' "$script")"
    [ "${#STEPS[@]}" -gt 0 ] || fail "no STEPS registry found in $script"
    for step in "${STEPS[@]}"; do
        for prefix in check "do" default; do
            declare -F "${prefix}_${step//-/_}" >/dev/null ||
                fail "step '$step' in $script has no ${prefix}_${step//-/_} function"
        done
    done
}
check_registry "$PVE_SETUP"
check_registry "$SETUP"

# The test machine is never a Proxmox host, so proxmox_setup.sh must refuse
# with a clear message instead of running a check.
if out="$(bash "$PVE_SETUP" --status 2>&1)"; then
    fail "proxmox_setup.sh --status ran off a Proxmox host"
fi
grep -q 'not a Proxmox VE host' <<<"$out" || fail "proxmox_setup.sh refused without saying why: $out"

# --status only runs the checks. SETUP_UPDATED skips the self-update, so the
# test never clones or re-execs.
bash "$SETUP" --status >/dev/null || fail "--status exited nonzero"

# A non-login shell lacks ~/.local/bin; --status must still find tools there.
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/.local/bin"
printf '#!/bin/sh\n' >"$tmp/.local/bin/herdr"
chmod +x "$tmp/.local/bin/herdr"
env -i HOME="$tmp" PATH=/usr/bin:/bin SETUP_UPDATED=1 bash "$SETUP" --status --only herdr |
    grep -Eq '^herdr +done$' || fail "--status misses ~/.local/bin/herdr in a clean env"

echo "ok: ${#STEPS[@]} steps"
