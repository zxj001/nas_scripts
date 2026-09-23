#!/usr/bin/env bash
# Behaviour checks on setup_adapters/pve_repos.sh, the Proxmox repository
# repair behind the repos task. Every host path is redirected into a temp
# fixture (PVE_SETUP_PREFIX) and apt-get/runuser are stubs, so nothing here
# changes the machine it runs on. Run from anywhere.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# PVE_REPOS_ADAPTER lets tests/test_setup_sh.py run this suite on a mutant.
SCRIPT="${PVE_REPOS_ADAPTER:-$ROOT/setup_adapters/pve_repos.sh}"
fail() {
    echo "FAIL: $*" >&2
    exit 1
}

F="$(mktemp -d)"
trap 'rm -rf "$F"' EXIT
export PVE_SETUP_PREFIX="$F"

# CI runs on Ubuntu with APT installed. On other development hosts the stubbed
# checks still run, but cannot establish real APT parsing or migration success.
APT_CACHE="$(command -v apt-cache || true)"
if [ -z "$APT_CACHE" ]; then
    if [ "${GITHUB_ACTIONS:-}" = true ]; then fail "CI requires apt-cache for repository parser checks"; fi
    echo "skip: real APT parser unavailable; real PVE migration remains unverified"
fi

assert_apt_sources() {
    local parser="$F/apt-parser" rc=0
    [ -n "$APT_CACHE" ] || return 0
    rm -rf "$parser"
    mkdir -p "$parser/etc/sources.list.d" "$parser/etc/apt.conf.d" \
        "$parser/state/lists/partial" "$parser/cache" "$parser/log" "$parser/methods"
    : >"$parser/state/status"
    : >"$parser/etc/sources.list"
    # Parse exact copies of the generated PVE sources and administrator entries
    # under test, without the unrelated synthetic Debian fixtures.
    cp "$@" "$parser/etc/sources.list.d/"
    # APT_CONFIG is loaded BEFORE apt.conf.d; command-line overrides alone
    # would still read host configuration and hooks. All mutable state is local.
    cat >"$parser/apt.conf" <<EOF
Dir "$parser";
Dir::Etc "$parser/etc";
Dir::State "$parser/state";
Dir::State::status "$parser/state/status";
Dir::Cache "$parser/cache";
Dir::Log "$parser/log";
Dir::Bin::methods "$parser/methods";
APT::Architecture "amd64";
APT::Architectures { "amd64"; };
EOF
    # policy builds the source list offline; no update/install or acquisition
    # occurs. Empty methods additionally prevent any network transport execution.
    APT_CONFIG="$parser/apt.conf" LC_ALL=C "$APT_CACHE" policy \
        >"$parser/output" 2>"$parser/error" || rc=$?
    if [ "$rc" != 0 ]; then
        cat "$parser/error" >&2
        fail "real APT rejected compatible sources"
    fi
}

# The adapter's definitions, then stubs that shadow the real commands, then a
# small stand-in for the runner: --status prints "repos done|todo"; otherwise
# the step runs when its check says todo, with errexit active as in the task.
HARNESS="$F/harness.sh"
{
    grep -v '^pve_repos_main "\$@"$' "$SCRIPT"
    cat <<'STUBS'
F="$PVE_SETUP_PREFIX"
runuser() {
    [ "$#" = 6 ] && [ "$1 $2 $3 $4 $5" = "-u _apt -- test -r" ] || return 99
    [ "${KEY_DENY:-}" != all ] && [ "${KEY_DENY:-}" != "$6" ] && [ -r "$6" ]
}
apt-get() {
    echo "apt-get $*" >>"$F/calls"
    return "${APT_RC:-0}"
}
# HARNESS_CALL=do_repos runs one function directly, bypassing the check.
if [ -n "${HARNESS_CALL:-}" ]; then
    "$HARNESS_CALL"
    exit
fi
status=0
for arg in "$@"; do [ "$arg" != --status ] || status=1; done
rc=0
check_repos || rc=$?
if [ "$status" = 1 ]; then
    if [ "$rc" = 0 ]; then echo "repos done"; else echo "repos todo"; fi
    exit 0
fi
if [ "$rc" != 0 ]; then
    do_repos
fi
STUBS
} >"$HARNESS"

run() { bash "$HARNESS" "$@"; }
status_of() { run --status --only "$1" | awk -v s="$1" '$1 == s { print $2 }'; }
calls() { cat "$F/calls" 2>/dev/null || true; }

# --- repos --------------------------------------------------------------------
A="$F/etc/apt/sources.list.d"
mkdir -p "$A"
snapshot() { cat "$F/etc/os-release" "$F/etc/apt/sources.list" "$A"/* 2>/dev/null | cksum; }
fresh_apt() {
    rm -rf "$A" "$F/calls"
    mkdir -p "$A" "$F/usr/share/keyrings" "$F/etc/apt/trusted.gpg.d"
    rm -f "$F/usr/share/keyrings/proxmox-archive-keyring.gpg" "$F/etc/apt/trusted.gpg.d/proxmox-release-bookworm.gpg"
    printf 'fixture archive key\n' >"$F/usr/share/keyrings/proxmox-archive-keyring.gpg"
    printf 'PRETTY_NAME="Debian GNU/Linux"\nVERSION_CODENAME=%s\n' "$1" >"$F/etc/os-release"
    printf 'Types: deb\nURIs: http://deb.debian.org/debian/\nSuites: %s %s-updates\nComponents: main contrib\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' \
        "$1" "$1" >"$A/debian.sources"
    printf 'deb http://deb.debian.org/debian %s main\n' "$1" >"$F/etc/apt/sources.list"
}

# 11. PVE 9 (deb822) fresh install, Ceph repo present.
fresh_apt trixie
cat >"$A/pve-enterprise.sources" <<'EOF'
# managed by the installer
Types: deb
URIs: https://enterprise.proxmox.com/debian/pve
Suites: trixie
Components: pve-enterprise
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF
cat >"$A/ceph.sources" <<'EOF'
Types: deb
URIs: https://enterprise.proxmox.com/debian/ceph-squid
Suites: trixie
Components: enterprise
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
Enabled: yes
EOF
debian_before="$(cksum <"$A/debian.sources")"
list_before="$(cksum <"$F/etc/apt/sources.list")"
[ "$(status_of repos)" = todo ] || fail "repos done with the enterprise repos enabled"
run --yes --only repos >/dev/null || fail "repos step failed on a fresh PVE 9 layout"
calls | grep -qx 'apt-get update' || fail "apt-get update not run"
[ "$(tail -n1 "$A/pve-enterprise.sources")" = "Enabled: false" ] || fail "pve-enterprise.sources not disabled"
head -n1 "$A/pve-enterprise.sources" | grep -qx '# managed by the installer' || fail "pve-enterprise.sources comment lost"
grep -qx 'Components: pve-enterprise' "$A/pve-enterprise.sources" || fail "pve-enterprise.sources fields lost"
[ "$(grep -i '^enabled:' "$A/ceph.sources")" = "Enabled: false" ] || fail "ceph.sources not disabled exactly once"
[ "$(cksum <"$A/debian.sources")" = "$debian_before" ] || fail "debian.sources touched"
[ "$(cksum <"$F/etc/apt/sources.list")" = "$list_before" ] || fail "sources.list touched"
cat >"$F/want" <<'EOF'
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: trixie
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg

Types: deb
URIs: http://download.proxmox.com/debian/ceph-squid
Suites: trixie
Components: no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF
cmp -s "$F/want" "$A/pve-no-subscription.sources" ||
    fail "pve-no-subscription.sources: $(cat "$A/pve-no-subscription.sources")"
[ "$(status_of repos)" = "done" ] || fail "repos not done after the step"
assert_apt_sources "$A/pve-no-subscription.sources" "$A/pve-enterprise.sources" "$A/ceph.sources"

# 12. Idempotent: the runner skips it, and do_repos itself changes nothing.
before="$(snapshot)"
rm -f "$F/calls"
run --yes --only repos >/dev/null
[ -z "$(calls)" ] || fail "repos reran although done"
HARNESS_CALL=do_repos run >/dev/null || fail "do_repos rerun failed"
[ "$(snapshot)" = "$before" ] || fail "do_repos rerun changed the sources"
[ "$(status_of repos)" = "done" ] || fail "repos not done after a rerun"

# 13. The check reads what apt would use: re-enabled enterprise, a no-subscription
# repo for another suite, or a disabled one, are all todo.
sed -i.bak 's/^Enabled: false$/Enabled: yes/' "$A/pve-enterprise.sources"
[ "$(status_of repos)" = todo ] || fail "repos done with pve-enterprise enabled"
mv "$A/pve-enterprise.sources.bak" "$A/pve-enterprise.sources"
printf 'PRETTY_NAME="Debian"\nVERSION_CODENAME="forky"\n' >"$F/etc/os-release"
[ "$(status_of repos)" = todo ] || fail "repos done with no-subscription for another suite"
printf 'VERSION_CODENAME=trixie\n' >"$F/etc/os-release"
sed -i.bak '5a\
Enabled: no
' "$A/pve-no-subscription.sources"
[ "$(status_of repos)" = todo ] || fail "repos done with no-subscription disabled"
rm -f "$A"/*.bak

# 14. PVE 8 (.list) layout on bookworm, no Ceph repo; apt-get failing fails the step.
fresh_apt bookworm
printf '# keep me\ndeb https://enterprise.proxmox.com/debian/pve bookworm pve-enterprise\n' >"$A/pve-enterprise.list"
[ "$(status_of repos)" = todo ] || fail "repos done with the .list enterprise repo enabled"
if APT_RC=100 run --yes --only repos >/dev/null 2>&1; then fail "apt-get update failure not reported"; fi
rm -f "$A/pve-no-subscription.sources"
# Start over from the uncommented file.
printf '# keep me\ndeb https://enterprise.proxmox.com/debian/pve bookworm pve-enterprise\n' >"$A/pve-enterprise.list"
run --yes --only repos >/dev/null || fail "repos step failed on a PVE 8 layout"
[ "$(cat "$A/pve-enterprise.list")" = "$(printf '# keep me\n# deb https://enterprise.proxmox.com/debian/pve bookworm pve-enterprise')" ] ||
    fail "pve-enterprise.list not commented in place: $(cat "$A/pve-enterprise.list")"
grep -qx 'Suites: bookworm' "$A/pve-no-subscription.sources" || fail "suite not taken from os-release"
if grep -q ceph "$A/pve-no-subscription.sources"; then fail "Ceph repo added without a Ceph enterprise repo"; fi
[ "$(status_of repos)" = "done" ] || fail "repos not done on the PVE 8 layout"
assert_apt_sources "$A/pve-no-subscription.sources" "$A/pve-enterprise.list"
before="$(snapshot)"
HARNESS_CALL=do_repos run >/dev/null
[ "$(snapshot)" = "$before" ] || fail "do_repos rerun changed the .list layout"

# 15. No-subscription already enabled in another file (what the PVE 9 web UI
# writes): done, and no duplicate is added.
fresh_apt trixie
printf 'Types: deb\nURIs: http://download.proxmox.com/debian/pve/\nSuites: trixie\nComponents: pve-no-subscription\nSigned-By: /usr/share/keyrings/proxmox-archive-keyring.gpg\n' >"$A/proxmox.sources"
[ "$(status_of repos)" = "done" ] || fail "existing no-subscription repo not recognised"
HARNESS_CALL=do_repos run >/dev/null
[ ! -e "$A/pve-no-subscription.sources" ] || fail "duplicate no-subscription repo written"

assert_repos_rerun() {
    local before
    before="$(snapshot)"
    rm -f "$F/calls"
    run --yes --only repos >/dev/null
    [ -z "$(calls)" ] || fail "completed repos step reran"
    HARNESS_CALL=do_repos run >/dev/null
    [ "$(snapshot)" = "$before" ] || fail "repos rerun changed source content"
    [ "$(status_of repos)" = "done" ] || fail "repos rerun did not remain done"
}

fresh_apt trixie
cat >"$A/proxmox.sources" <<'EOF'
Types: deb
URIs:
 http://download.proxmox.com/debian/pve
Suites:
 trixie
Components:
 pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
Enabled:
 yes
EOF
cp "$A/proxmox.sources" "$F/continued-public"
[ "$(status_of repos)" = "done" ] || fail "continued no-subscription fields not recognised"
cat >"$A/pve-enterprise.sources" <<'EOF'
# installer source
Types: deb
URIs:
 https://enterprise.proxmox.com/debian/pve
Suites:
 trixie
Components:
 pve-enterprise
Enabled:
 yes
EOF
[ "$(status_of repos)" = todo ] || fail "continued enterprise URI missed"
run --yes --only repos >/dev/null
calls | grep -qx 'apt-get update' || fail "continued enterprise source skipped remediation"
cat >"$F/want" <<'EOF'
# installer source
Types: deb
URIs:
 https://enterprise.proxmox.com/debian/pve
Suites:
 trixie
Components:
 pve-enterprise
Enabled: false
EOF
cmp -s "$F/want" "$A/pve-enterprise.sources" || fail "continued enterprise fields not preserved or disabled"
cmp -s "$F/continued-public" "$A/proxmox.sources" || fail "continued public source changed"
[ ! -e "$A/pve-no-subscription.sources" ] || fail "continued public source duplicated"
[ "$(status_of repos)" = "done" ] || fail "continued enterprise source remains enabled"
assert_repos_rerun

for layout in sources list; do
    fresh_apt trixie
    if [ "$layout" = sources ]; then
        cat >"$A/ceph.sources" <<'EOF'
# deb https://enterprise.proxmox.com/debian/ceph-reef trixie enterprise
Types: deb
URIs: https://enterprise.proxmox.com/debian/ceph-reef
Suites: trixie
Components: enterprise
Enabled: false

Types: deb
URIs:
 https://enterprise.proxmox.com/debian/ceph-squid
Suites: trixie
Components: enterprise

Types: deb
URIs: https://mirror.example.org/other
Suites: trixie
Components: main
EOF
        cat >"$F/want-ceph" <<'EOF'
# deb https://enterprise.proxmox.com/debian/ceph-reef trixie enterprise
Types: deb
URIs: https://enterprise.proxmox.com/debian/ceph-reef
Suites: trixie
Components: enterprise
Enabled: false

Types: deb
URIs:
 https://enterprise.proxmox.com/debian/ceph-squid
Suites: trixie
Components: enterprise
Enabled: false

Types: deb
URIs: https://mirror.example.org/other
Suites: trixie
Components: main
EOF
    else
        cat >"$A/ceph.list" <<'EOF'
# deb https://enterprise.proxmox.com/debian/ceph-reef trixie enterprise
deb https://enterprise.proxmox.com/debian/ceph-squid trixie enterprise
deb https://mirror.example.org/other trixie main
EOF
        cat >"$F/want-ceph" <<'EOF'
# deb https://enterprise.proxmox.com/debian/ceph-reef trixie enterprise
# deb https://enterprise.proxmox.com/debian/ceph-squid trixie enterprise
deb https://mirror.example.org/other trixie main
EOF
    fi
    run --yes --only repos >/dev/null
    cmp -s "$F/want-ceph" "$A/ceph.$layout" || fail "Ceph $layout content not preserved or disabled"
    cat >"$F/want" <<'EOF'
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: trixie
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg

Types: deb
URIs: http://download.proxmox.com/debian/ceph-squid
Suites: trixie
Components: no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF
    cmp -s "$F/want" "$A/pve-no-subscription.sources" || fail "historical Ceph release selected in $layout layout"
    [ "$(status_of repos)" = "done" ] || fail "Ceph $layout migration incomplete"
    assert_repos_rerun
done

fresh_apt trixie
printf 'Types: deb\nURIs: http://download.proxmox.com/debian/pve\nSuites: trixie\nComponents: pve-no-subscription\nEnabled: no' >"$A/pve-no-subscription.sources"
cp "$A/pve-no-subscription.sources" "$F/want"
cat >>"$F/want" <<'EOF'


Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: trixie
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF
[ "$(status_of repos)" = todo ] || fail "disabled unterminated source counted as done"
run --yes --only repos >/dev/null
cmp -s "$F/want" "$A/pve-no-subscription.sources" || fail "appended stanza not separated from unterminated content"
[ "$(status_of repos)" = "done" ] || fail "appended stanza remains disabled"
assert_repos_rerun

for layout in sources list; do
    fresh_apt bookworm
    printf 'deb https://enterprise.proxmox.com/debian/pve bookworm pve-enterprise\n' >"$A/pve-enterprise.list"
    if [ "$layout" = sources ]; then
        cat >"$A/ceph.sources" <<'EOF'
Types: deb
URIs:
 https://enterprise.proxmox.com/debian/ceph-reef
Suites: bookworm
Components: enterprise
Enabled: false
EOF
    else
        printf '# deb [signed-by=/keyring] https://enterprise.proxmox.com/debian/ceph-reef bookworm enterprise # disabled\n' >"$A/ceph.list"
    fi
    cp "$A/ceph.$layout" "$F/disabled-ceph"
    run --yes --only repos >/dev/null
    cmp -s "$F/disabled-ceph" "$A/ceph.$layout" || fail "disabled Ceph $layout changed"
    cat >"$F/want" <<'EOF'
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: bookworm
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg

Types: deb
URIs: http://download.proxmox.com/debian/ceph-reef
Suites: bookworm
Components: no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF
    cmp -s "$F/want" "$A/pve-no-subscription.sources" || fail "disabled Ceph $layout fallback missing"
    assert_repos_rerun
done

for protected in debian.sources sources.list; do
    fresh_apt trixie
    printf 'deb https://enterprise.proxmox.com/debian/pve trixie pve-enterprise\n' >"$A/pve-enterprise.list"
    printf 'deb [signed-by=/usr/share/keyrings/proxmox-archive-keyring.gpg] http://download.proxmox.com/debian/pve trixie pve-no-subscription\n' >"$A/public.list"
    if [ "$protected" = debian.sources ]; then
        cat >>"$A/debian.sources" <<'EOF'

Types: deb
URIs:
 https://enterprise.proxmox.com/debian/pve
Suites: trixie
Components: pve-enterprise
EOF
    else
        printf 'deb [arch=amd64] https://enterprise.proxmox.com/debian/pve trixie pve-enterprise\n' >>"$F/etc/apt/sources.list"
    fi
    before="$(snapshot)"
    if run --yes --only repos >"$F/output" 2>&1; then fail "protected $protected layout accepted"; fi
    grep -q 'unsupported enterprise repository in protected file:' "$F/output" || fail "protected layout not explained"
    [ "$(snapshot)" = "$before" ] || fail "source mutation before protected $protected refusal"
    [ -z "$(calls)" ] || fail "apt called for protected $protected layout"
    rm "$A/pve-enterprise.list"
    [ "$(status_of repos)" = todo ] || fail "enterprise in protected $protected missed by check"
    before="$(snapshot)"
    if HARNESS_CALL=do_repos run >/dev/null 2>&1; then fail "direct call accepted protected $protected"; fi
    [ "$(snapshot)" = "$before" ] || fail "direct call changed protected layout"
    [ -z "$(calls)" ] || fail "direct call ran apt for protected layout"
done

fresh_apt trixie
cat >>"$A/debian.sources" <<'EOF'

Types: deb
URIs: https://enterprise.proxmox.com/debian/pve
Suites: trixie
Components: pve-enterprise
Enabled: no
EOF
printf '# deb https://enterprise.proxmox.com/debian/pve trixie pve-enterprise\n' >>"$F/etc/apt/sources.list"
cp "$A/debian.sources" "$F/protected-debian"
cp "$F/etc/apt/sources.list" "$F/protected-list"
printf 'deb https://enterprise.proxmox.com/debian/pve trixie pve-enterprise\n' >"$A/pve-enterprise.list"
run --yes --only repos >/dev/null
cmp -s "$F/protected-debian" "$A/debian.sources" || fail "disabled protected deb822 changed"
cmp -s "$F/protected-list" "$F/etc/apt/sources.list" || fail "disabled protected list changed"
assert_repos_rerun

fresh_apt bookworm
cat >"$A/unrelated.list" <<'EOF'
deb [arch=amd64] http://deb.debian.org/debian bookworm main # https://enterprise.proxmox.com/debian/ceph-reef configured separately
# Historical URI: https://enterprise.proxmox.com/debian/ceph-squid
deb [signed-by=/usr/share/keyrings/proxmox-archive-keyring.gpg] http://download.proxmox.com/debian/pve bookworm main # pve-no-subscription
EOF
cat >"$A/unrelated.sources" <<'EOF'
Types: deb
URIs: https://mirror.example.org/other
Suites: bookworm
Components: main
Description: enterprise.proxmox.com is configured separately
EOF
cp "$A/unrelated.list" "$F/unrelated-list"
cp "$A/unrelated.sources" "$F/unrelated-sources"
[ "$(status_of repos)" = todo ] || fail "inline component comment counted as source"
printf 'deb [signed-by=/usr/share/keyrings/proxmox-archive-keyring.gpg] http://download.proxmox.com/debian/pve bookworm pve-no-subscription\n' >"$A/public.list"
[ "$(status_of repos)" = "done" ] || fail "enterprise comment counted as enabled source"
printf 'deb [signed-by=/keyring] https://enterprise.proxmox.com/debian/pve bookworm pve-enterprise # keep note\n' >"$A/pve-enterprise.list"
cat "$F/unrelated-list" >>"$A/pve-enterprise.list"
run --yes --only repos >/dev/null
cmp -s "$F/unrelated-list" "$A/unrelated.list" || fail "unrelated list entry or comment changed"
cmp -s "$F/unrelated-sources" "$A/unrelated.sources" || fail "unrelated deb822 field changed"
[ ! -e "$A/pve-no-subscription.sources" ] || fail "comment URI caused a replacement source"
printf '# deb [signed-by=/keyring] https://enterprise.proxmox.com/debian/pve bookworm pve-enterprise # keep note\n' >"$F/want"
cat "$F/unrelated-list" >>"$F/want"
cmp -s "$F/want" "$A/pve-enterprise.list" || fail "enterprise URI not disabled with options and inline comment"
assert_repos_rerun

ARCHIVE_KEY=/usr/share/keyrings/proxmox-archive-keyring.gpg
LEGACY_KEY=/etc/apt/trusted.gpg.d/proxmox-release-bookworm.gpg
keyring_enterprise_fixture() {
    printf 'deb https://enterprise.proxmox.com/debian/pve %s pve-enterprise\n' "$1" >"$A/pve-enterprise.list"
    printf 'deb https://enterprise.proxmox.com/debian/ceph-reef %s enterprise\n' "$1" >"$A/ceph.list"
}
expected_keyring_sources() {
    cat <<EOF
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: bookworm
Components: pve-no-subscription
Signed-By: $1

Types: deb
URIs: http://download.proxmox.com/debian/ceph-reef
Suites: bookworm
Components: no-subscription
Signed-By: $1
EOF
}

for key_case in prefer-archive legacy-only unreadable-archive; do
    fresh_apt bookworm
    printf 'fixture legacy key\n' >"$F$LEGACY_KEY"
    keyring_enterprise_fixture bookworm
    selected="$ARCHIVE_KEY"
    case "$key_case" in
        legacy-only) rm "$F$ARCHIVE_KEY"; selected="$LEGACY_KEY" ;;
        unreadable-archive) export KEY_DENY="$F$ARCHIVE_KEY"; selected="$LEGACY_KEY" ;;
    esac
    run --yes --only repos >/dev/null
    expected_keyring_sources "$selected" >"$F/want"
    cmp -s "$F/want" "$A/pve-no-subscription.sources" || fail "wrong generated Signed-By for $key_case"
    calls | grep -qx 'apt-get update' || fail "apt not called for $key_case"
    assert_repos_rerun
    unset KEY_DENY
done

for key_case in legacy-on-trixie missing empty unreadable; do
    fresh_apt bookworm
    if [ "$key_case" = legacy-on-trixie ]; then fresh_apt trixie; fi
    keyring_enterprise_fixture "$(sed -n 's/^VERSION_CODENAME=//p' "$F/etc/os-release")"
    case "$key_case" in
        legacy-on-trixie) rm "$F$ARCHIVE_KEY"; printf 'legacy key\n' >"$F$LEGACY_KEY" ;;
        missing) rm "$F$ARCHIVE_KEY" ;;
        empty) : >"$F$ARCHIVE_KEY"; : >"$F$LEGACY_KEY" ;;
        unreadable) printf 'legacy key\n' >"$F$LEGACY_KEY"; export KEY_DENY=all ;;
    esac
    before="$(snapshot)"
    if run --yes --only repos >"$F/output" 2>&1; then fail "unusable key accepted: $key_case"; fi
    grep -q 'no eligible Proxmox keyring' "$F/output" || fail "key refusal not explained"
    [ "$(snapshot)" = "$before" ] || fail "sources changed before key refusal: $key_case"
    [ -z "$(calls)" ] || fail "apt called without usable key: $key_case"
    unset KEY_DENY
done

fresh_apt bookworm
rm "$F$ARCHIVE_KEY"
printf 'legacy key\n' >"$F$LEGACY_KEY"
expected_keyring_sources "$ARCHIVE_KEY" >"$A/pve-no-subscription.sources"
cat >>"$A/pve-no-subscription.sources" <<'EOF'

# unrelated stanza retains its own trust
Types: deb
URIs: https://mirror.example.org/other
Suites: bookworm
Components: main
Signed-By: /custom/keyring.gpg
EOF
cp "$A/pve-no-subscription.sources" "$F/managed-before"
[ "$(status_of repos)" = todo ] || fail "managed missing Signed-By key counted as done"
run --yes --only repos >/dev/null
sed "s|$ARCHIVE_KEY|$LEGACY_KEY|g" "$F/managed-before" >"$F/want"
cmp -s "$F/want" "$A/pve-no-subscription.sources" || fail "managed stanzas not repaired in place"
calls | grep -qx 'apt-get update' || fail "managed repair skipped apt"
assert_repos_rerun
printf 'archive key\n' >"$F$ARCHIVE_KEY"
[ "$(status_of repos)" = todo ] || fail "managed legacy key preferred over installed archive key"
run --yes --only repos >/dev/null
cmp -s "$F/managed-before" "$A/pve-no-subscription.sources" || fail "managed stanzas not updated to preferred archive key"
assert_repos_rerun
export KEY_DENY=all
[ "$(status_of repos 2>/dev/null)" = todo ] || fail "managed unreadable key counted as done"
before="$(snapshot)"
rm -f "$F/calls"
if run --yes --only repos >/dev/null 2>&1; then fail "managed unreadable key accepted"; fi
[ "$(snapshot)" = "$before" ] || fail "managed sources changed with no usable key"
[ -z "$(calls)" ] || fail "managed unusable key invoked apt"
unset KEY_DENY

for layout in sources list; do
    for ceph_state in absent source-only binary-and-source; do
        fresh_apt bookworm
        if [ "$layout" = sources ]; then
            cat >"$A/public.sources" <<'EOF'
Types: deb-src
 deb
URIs: http://download.proxmox.com/debian/pve
Suites: bookworm
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF
            cat >"$A/ceph.sources" <<'EOF'
Types: deb
URIs: https://enterprise.proxmox.com/debian/ceph-squid
Suites: bookworm
Components: enterprise
Enabled: false
EOF
            if [ "$ceph_state" != absent ]; then
                types=deb-src
                if [ "$ceph_state" = binary-and-source ]; then types='deb-src deb'; fi
                cat >>"$A/public.sources" <<EOF

Types: $types
URIs: http://download.proxmox.com/debian/ceph-squid
Suites: bookworm
Components: no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF
            fi
        else
            printf 'deb [signed-by=/usr/share/keyrings/proxmox-archive-keyring.gpg] http://download.proxmox.com/debian/pve bookworm pve-no-subscription\n' >"$A/public.list"
            printf '# deb https://enterprise.proxmox.com/debian/ceph-squid bookworm enterprise\n' >"$A/ceph.list"
            if [ "$ceph_state" != absent ]; then
                printf 'deb-src [signed-by=/usr/share/keyrings/proxmox-archive-keyring.gpg] http://download.proxmox.com/debian/ceph-squid bookworm no-subscription\n' >>"$A/public.list"
            fi
            if [ "$ceph_state" = binary-and-source ]; then
                printf 'deb [signed-by=/usr/share/keyrings/proxmox-archive-keyring.gpg] http://download.proxmox.com/debian/ceph-squid bookworm no-subscription\n' >>"$A/public.list"
            fi
        fi
        cp "$A/public.$layout" "$F/public-before"
        cp "$A/ceph.$layout" "$F/ceph-before"
        if [ "$ceph_state" = binary-and-source ]; then
            [ "$(status_of repos)" = "done" ] || fail "binary and source types not recognised: $layout"
        else
            [ "$(status_of repos)" = todo ] || fail "missing Ceph binary counted as done: $layout $ceph_state"
            run --yes --only repos >/dev/null
            cat >"$F/want" <<'EOF'
Types: deb
URIs: http://download.proxmox.com/debian/ceph-squid
Suites: bookworm
Components: no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF
            cmp -s "$F/want" "$A/pve-no-subscription.sources" || fail "missing Ceph binary not added: $layout $ceph_state"
            calls | grep -qx 'apt-get update' || fail "Ceph binary remediation skipped apt"
        fi
        cmp -s "$F/public-before" "$A/public.$layout" || fail "existing public entries changed"
        cmp -s "$F/ceph-before" "$A/ceph.$layout" || fail "disabled Ceph entry changed"
        assert_repos_rerun
        if [ "$ceph_state" = binary-and-source ]; then
            [ ! -e "$A/pve-no-subscription.sources" ] || fail "mixed binary/source entry duplicated"
        fi
    done

    fresh_apt bookworm
    keyring_enterprise_fixture bookworm
    if [ "$layout" = sources ]; then
        cat >"$A/public.sources" <<'EOF'
Types: deb-src
URIs: http://download.proxmox.com/debian/pve
Suites: bookworm
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg

Types: deb-src
URIs: http://download.proxmox.com/debian/ceph-reef
Suites: bookworm
Components: no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF
    else
        printf 'deb-src [signed-by=/usr/share/keyrings/proxmox-archive-keyring.gpg] http://download.proxmox.com/debian/pve bookworm pve-no-subscription\ndeb-src [signed-by=/usr/share/keyrings/proxmox-archive-keyring.gpg] http://download.proxmox.com/debian/ceph-reef bookworm no-subscription\n' >"$A/public.list"
    fi
    cp "$A/public.$layout" "$F/public-before"
    run --yes --only repos >/dev/null
    expected_keyring_sources "$ARCHIVE_KEY" >"$F/want"
    cmp -s "$F/want" "$A/pve-no-subscription.sources" || fail "deb-src prevented binary replacement: $layout"
    cmp -s "$F/public-before" "$A/public.$layout" || fail "source-only entries changed"
    assert_apt_sources "$A/pve-no-subscription.sources" "$A/public.$layout"
    assert_repos_rerun
    rm "$A/pve-no-subscription.sources"
    [ "$(status_of repos)" = todo ] || fail "source-only PVE counted as binary: $layout"
done

# APT accepts an absent Signed-By followed by an explicit key, but rejects
# the reverse order. Sort the administrator entry after the managed file so
# the absent-key case exercises that conflict for both source formats.
for layout in list sources; do
    for repo in pve ceph-reef; do
        for signing in absent different fingerprint; do
            fresh_apt bookworm
            expected_keyring_sources "$ARCHIVE_KEY" >"$A/pve-no-subscription.sources"
            component=no-subscription
            if [ "$repo" = pve ]; then component=pve-no-subscription; fi
            setting=""
            case "$signing" in
                different) setting=/custom/admin-keyring.gpg ;;
                fingerprint) setting="$ARCHIVE_KEY 0123456789ABCDEF0123456789ABCDEF01234567" ;;
            esac
            if [ "$layout" = list ]; then
                options=""
                if [ -n "$setting" ]; then options="[arch=amd64 signed-by=${setting// /,}] "; fi
                printf 'deb-src %shttp://download.proxmox.com/debian/%s bookworm %s\n' "$options" "$repo" "$component" >"$A/z-public.list"
            else
                cat >"$A/z-public.sources" <<EOF
Types: deb-src
URIs: http://download.proxmox.com/debian/$repo
Suites: bookworm
Components: $component
EOF
                if [ -n "$setting" ]; then printf 'Signed-By: %s\n' "$setting" >>"$A/z-public.sources"; fi
            fi
            [ "$(status_of repos 2>/dev/null)" = todo ] || fail "conflicting $layout $repo $signing counted as done"
            keyring_enterprise_fixture bookworm
            rm -rf "$F/apt-before"
            cp -R "$F/etc/apt" "$F/apt-before"
            if run --yes --only repos >"$F/output" 2>&1; then fail "conflicting signing accepted: $layout $repo $signing"; fi
            grep -q 'conflicting Signed-By' "$F/output" || fail "signing conflict not explained"
            diff -r "$F/apt-before" "$F/etc/apt" || fail "signing refusal changed source files"
            [ -z "$(calls)" ] || fail "signing refusal invoked apt"
            if HARNESS_CALL=do_repos run >/dev/null 2>&1; then fail "direct call accepted signing conflict"; fi
            diff -r "$F/apt-before" "$F/etc/apt" || fail "direct refusal changed source files"
            [ -z "$(calls)" ] || fail "direct signing refusal invoked apt"
        done
    done
done

fresh_apt bookworm
expected_keyring_sources "$ARCHIVE_KEY" >"$A/pve-no-subscription.sources"
cat >"$A/public.sources" <<'EOF'
Types: deb-src
URIs: http://download.proxmox.com/debian/pve/
Suites: bookworm
Components: pve-no-subscription
Signed-By:
 /usr/share/keyrings/proxmox-archive-keyring.gpg

Types: deb-src
URIs: http://download.proxmox.com/debian/pve
Suites: forky
Components: pve-no-subscription
Signed-By: /other-suite-keyring.gpg

Types: deb-src
URIs: http://download.proxmox.com/debian/ceph-reef
Suites: bookworm
Components: no-subscription
Enabled: false
EOF
[ "$(status_of repos)" = "done" ] || fail "compatible continued signing or inactive entry rejected"
assert_repos_rerun

fresh_apt trixie
expected_keyring_sources "$LEGACY_KEY" >"$A/pve-no-subscription.sources"
cat >>"$A/pve-no-subscription.sources" <<'EOF'

Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: trixie
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF
printf 'deb-src [signed-by=%s] http://download.proxmox.com/debian/pve bookworm pve-no-subscription\n' "$LEGACY_KEY" >"$A/admin.list"
rm -rf "$F/apt-before"
cp -R "$F/etc/apt" "$F/apt-before"
[ "$(status_of repos)" = "done" ] || fail "retained Bookworm signing marked incomplete on Trixie"
assert_repos_rerun
diff -r "$F/apt-before" "$F/etc/apt" || fail "upgrade changed retained Bookworm or administrator sources"

fresh_apt bookworm
rm "$F$ARCHIVE_KEY"
printf 'legacy key\n' >"$F$LEGACY_KEY"
expected_keyring_sources "$ARCHIVE_KEY" >"$A/pve-no-subscription.sources"
cat >"$F/other-suite" <<'EOF'

# preserve this old release
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: bullseye
Components: pve-no-subscription
Signed-By: /custom/bullseye-keyring.gpg
EOF
cat "$F/other-suite" >>"$A/pve-no-subscription.sources"
expected_keyring_sources "$LEGACY_KEY" >"$F/want"
cat "$F/other-suite" >>"$F/want"
run --yes --only repos >/dev/null
cmp -s "$F/want" "$A/pve-no-subscription.sources" || fail "running-suite repair changed other-suite stanza"
assert_repos_rerun

for mixed_key in "$LEGACY_KEY" "$ARCHIVE_KEY"; do
    fresh_apt trixie
    cat >"$A/pve-no-subscription.sources" <<EOF
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: bookworm
 trixie
Components: pve-no-subscription
Signed-By: $mixed_key
EOF
    if [ "$mixed_key" = "$ARCHIVE_KEY" ]; then
        [ "$(status_of repos)" = "done" ] || fail "compatible mixed-suite stanza rejected"
        assert_repos_rerun
        continue
    fi
    [ "$(status_of repos 2>/dev/null)" = todo ] || fail "mixed-suite repair counted as done"
    keyring_enterprise_fixture trixie
    rm -rf "$F/apt-before"
    cp -R "$F/etc/apt" "$F/apt-before"
    if run --yes --only repos >"$F/output" 2>&1; then fail "unsafe mixed-suite repair accepted"; fi
    grep -q 'cannot repair Signed-By in mixed-suite managed stanza' "$F/output" || fail "mixed-suite refusal not explained"
    diff -r "$F/apt-before" "$F/etc/apt" || fail "mixed-suite refusal changed sources"
    [ -z "$(calls)" ] || fail "mixed-suite refusal invoked apt"
    if HARNESS_CALL=do_repos run >/dev/null 2>&1; then fail "direct call accepted mixed-suite repair"; fi
    diff -r "$F/apt-before" "$F/etc/apt" || fail "direct mixed-suite refusal changed sources"
    [ -z "$(calls)" ] || fail "direct mixed-suite refusal invoked apt"
done

echo "ok: pve_repos.sh behaviour"
