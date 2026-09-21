#!/usr/bin/env bash
# Checks on scripts/setup.sh. Run from anywhere: tests/test_setup.sh
set -euo pipefail

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

# Every registered step needs its check_/do_/default_ trio.
steps="$(sed -n 's/^STEPS=(\(.*\))$/\1/p' "$SETUP")"
[ -n "$steps" ] || fail "no STEPS registry found in $SETUP"
for step in $steps; do
    for prefix in check "do" default; do
        grep -q "^${prefix}_${step//-/_}()" "$SETUP" ||
            fail "step '$step' has no ${prefix}_${step//-/_} function"
    done
done

# --status only runs the checks. SETUP_UPDATED skips the self-update, so the
# test never clones or re-execs.
SETUP_UPDATED=1 bash "$SETUP" --status >/dev/null || fail "--status exited nonzero"

echo "ok: $(echo "$steps" | wc -w | tr -d ' ') steps"
