# Setup preservation and failure audit

Scope: the shared runner and every task in the Debian, macOS, and Proxmox profiles.
This supersedes the previous shell-only audit. See [runner behavior and commands](setup-runner.md).

| Area/tasks | Preservation and recovery contract |
| --- | --- |
| Runner/status/selection | Validate graph/selection before mutation. Read-only probes never update code or install prerequisites. Fresh process per operation; malformed/crashed workers fail visibly. Continue independent tasks; block only unmet capabilities or unsafe shared resources. |
| Resume/concurrency | Private atomic journals, host/user/version/input validation, re-probe all selected work. Common apply lock. Journals do not establish completion. |
| download-tools | Explicit curl/CA provider fixes fresh Debian installs. `--with-deps` selects it for downloads; literal `--only` reports missing dependencies instead of implicitly installing them. |
| sudo/brew | Preserve supplementary groups; new sudo membership defers until a new login. Discover existing Homebrew before installation. Append initialization once. |
| upgrade/packages | apt keeps modified conffiles and uses `--no-remove`. An unhealthy dpkg database blocks package consumers; unrelated user tasks continue. |
| guest-agent/no-sleep/ssh | Detect applicable guests/current service state; use repeatable enable/mask commands without forced replacement of custom units. |
| ssh-keys | Validate key fingerprints, preserve existing key material, deduplicate equivalent public keys, maintain permissions. Proxmox follows its cluster symlink and records operator fingerprints separately. |
| ssh-harden | Check effective policy; preserve differing existing config and report manual hardening. Require operator key; Proxmox also requires a successful recorded root login. Validate syntax/policy and reload before commit. Roll back on failure. |
| tailscale.install/login | Reuse installed client and tailnet state. Sign-in is separate, never resets preferences, and defers without a terminal. |
| subnet-router | Preserve non-default advertised subnets and unrelated sysctl lines; change only IPv4 forwarding. Roll back prior file/live forwarding on failure. Administrative route approval remains unverified. |
| directories/setup-command | Preserve directory contents and occupied launcher paths. Install a complete content-addressed bundle. No checkout update/reset/clean. |
| gh | Download full key before publication. Preserve existing keyring and conflicting apt source. No truncated source writes. |
| node | Honor NVM_DIR, preserve occupied incomplete installs and the default alias. Discover installed binaries explicitly between workers. Do not remove older Node versions. |
| codex/pi/claude/herdr.install | Reuse installed commands. Complete downloads before running vendor installers; Pi disables npm lifecycle scripts. Authentication remains a separate user action. |
| herdr.integration | Verify current integration using Herdr status. Preserve existing Claude settings if hooks need reconciliation. Only initialize absent settings automatically. |
| firstmate | Recognize checkout/worktree; preserve occupied non-checkout paths. Clone only when absent; no automatic pull/reset. |
| gpu | Reuse working NVIDIA driver. Add missing apt components conservatively without overwriting existing source files; defer pending reboot. |
| power-restore | Optional local IPMI/macOS changes with readback. Firmware configuration on normal desktops stays manual/unverified; VMs are not applicable. No reboot/shutdown/power cycle. |
| File writes | Stage/fsync and atomic publication. Exclusive creation preserves concurrently created operator files. Adjacent private recovery record persists until commit. Retry refuses intervening operator edits. |

## Limits

This is repeatable setup, not an immutable image or a whole-run transaction. Selected
upgrades and installers intentionally change state. Vendor code, package scripts and
existing nvm remain trusted dependencies. Recovery records protect managed file
changes, not arbitrary side effects of external commands. Failed rollback marks
shared resources unsafe; dependent work cannot claim success. Package cancellation
may wait for a transaction to settle rather than kill a database writer.

SSH probes inspect global policy on Debian and a root/localhost context on Proxmox;
they do not prove every Match rule or a new remote connection works. Verify a new
key-based session before closing an existing session. IPv4 route advertisement does
not prove admin approval or connectivity. GPU source detection is conservative text
matching rather than a complete apt/deb822 parser. Firmware settings need a planned
hardware test. A same-channel release checksum is integrity checking, not a separate
trust root.

## Validation boundaries

`test_setup_runner.py` covers scheduling, dependencies, verification, strict outcomes,
resource failures, readonly behavior, process isolation, journals and locks.
`test_setup_tasks.py` uses controlled command responses and fixture-only writes to
check preservation/rollback, SSH policy and cluster keys, routing, nvm, integration,
power classification and failed downloads. `test_setup_distribution.py` builds and
executes the real bundle outside a checkout. Shell tests inspect public entrypoints,
not sliced functions from the old monoliths.

Debian CI additionally exercises actual apt installs and systemd in a disposable
container, two runs, and missing dependencies. macOS CI exercises fixture task and
packaging contracts. Real Proxmox VM, firmware power recovery, SSH lockout/Match-rule,
and interactive account sign-in tests remain required before a production rollout.
