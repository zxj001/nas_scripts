#!/usr/bin/env bash
set -euo pipefail
: "${NVM_DIR:?explicit nvm directory required}"
# shellcheck source=/dev/null
. "$NVM_DIR/nvm.sh" --no-use
nvm install 22
if [ ! -e "$NVM_DIR/alias/default" ] && [ ! -L "$NVM_DIR/alias/default" ]; then
    nvm alias default 22
fi
