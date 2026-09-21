#!/usr/bin/env bash
# Test the public shell entrypoints; no source slicing or host mutation.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if command -v shellcheck >/dev/null 2>&1; then
    shellcheck "$ROOT/scripts/setup.sh" "$ROOT/scripts/proxmox_setup.sh" "$ROOT/setup_adapters/node.sh" "$ROOT/tests/integration/debian.sh" "${BASH_SOURCE[0]}"
fi
bash -n "$ROOT/scripts/setup.sh"
bash -n "$ROOT/scripts/proxmox_setup.sh"
bash "$ROOT/scripts/setup.sh" --help >/dev/null
bash "$ROOT/scripts/setup.sh" --plan --profile debian --only pi --with-deps >/dev/null
if bash "$ROOT/scripts/setup.sh" --plan --profile debian --only nonexistent >/dev/null 2>&1; then
    echo 'unknown task was accepted' >&2
    exit 1
fi
