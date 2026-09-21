#!/usr/bin/env bash
# Checks on scripts/setup.sh. Run from anywhere: tests/test_setup.sh
set -euo pipefail

# Never let anything below reach self_update: no clone, pull or exec.
export SETUP_UPDATED=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP="$ROOT/scripts/setup.sh"
fail() {
    echo "FAIL: $*" >&2
    exit 1
}

bash -n "$SETUP" || fail "syntax error"

if command -v shellcheck >/dev/null 2>&1; then
    shellcheck "$SETUP" "${BASH_SOURCE[0]}" || fail "shellcheck"
else
    echo "shellcheck not installed, skipped"
fi

# Every registered step needs its check_/do_/default_ trio. Load the script's
# definitions (everything but the `main "$@"` call) and ask bash for the
# functions it actually defined.
grep -q '^main "\$@"$' "$SETUP" || fail "no main \"\$@\" call found in $SETUP"
STEPS=()
eval "$(grep -v '^main "\$@"$' "$SETUP")"
[ "${#STEPS[@]}" -gt 0 ] || fail "no STEPS registry found in $SETUP"
for step in "${STEPS[@]}"; do
    for prefix in check "do" default; do
        declare -F "${prefix}_${step//-/_}" >/dev/null ||
            fail "step '$step' has no ${prefix}_${step//-/_} function"
    done
done

# --status only runs the checks. SETUP_UPDATED skips the self-update, so the
# test never clones or re-execs.
bash "$SETUP" --status >/dev/null || fail "--status exited nonzero"

echo "ok: ${#STEPS[@]} steps"
