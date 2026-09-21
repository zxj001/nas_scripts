# Setup runner

The machine and Proxmox scripts share a Python 3.11+ standard-library runner.
Each task runs in a fresh process. A failed task is reported immediately; independent
work continues. The final summary repeats every warning and unresolved action.

## Run from a checkout

```sh
bash scripts/setup.sh --plan --only tailscale --with-deps
bash scripts/setup.sh --only tailscale --with-deps
bash scripts/setup.sh --status
bash scripts/setup.sh --yes --only directories,git --with-deps --json
# On the Proxmox host, as root:
bash scripts/proxmox_setup.sh --only ssh-harden,tailscale --with-deps
```

Run machine setup as the target user, without wrapping it in sudo. The controller
requests sudo once when interactive; individual system commands elevate. `--yes`
does not supply credentials or automate sign-ins. Tasks requiring authentication or
missing input are deferred, with instructions, while other tasks continue.

`--only` is literal. For example, `--only pi` checks Node/npm and blocks Pi if they
are absent. `--only pi --with-deps` also selects Node and its prerequisite providers.
Group aliases `dev-tools`, `tailscale`, and `herdr` remain available. `--plan` lists
actual task IDs and dependencies without probing the machine. An explicit profile
allows reviewing another platform's plan; apply/status require a matching host.

`--status` only probes: no runtime installation, sudo prompt, journal, downloads,
package updates, checkout changes, or writes to configuration. Missing prerequisites
remain pending. `--help` and `--plan` likewise never bootstrap dependencies.

## Results and resume

| Outcome | Meaning |
| --- | --- |
| satisfied | A current probe verified the desired state. |
| changed | Apply completed and a fresh probe verified the desired state. |
| pending | Read-only inspection found work remaining. |
| failed | An operation, worker, or verification failed. |
| blocked | A prerequisite or shared resource is unavailable/unsafe. |
| manual | The task needs operator configuration/reconciliation; setup cannot verify completion. |
| deferred | Login, input, reboot, or another later action is needed. |
| skipped | The operator declined the selected task. |
| not-applicable | This machine does not support/need the task. |

Warnings are retained separately from outcomes. Apply exits 0 when every selected
outcome is complete, declined, or not applicable; 1 when work remains unresolved;
2 for invalid input/bootstrap/controller errors; 130 for cancellation. Status exits
0 with pending/manual work, but 1 for failed probes. `--fail-fast` stops scheduling
after the first failure. The default continues all independent work.

Apply prints a run ID and stores a private journal and redacted worker logs under
`~/.local/state/nas-setup` (0700 directory, 0600 files). `--state-dir` changes that
location. Authentication uses the terminal directly and is not logged. URLs and
common token/password fields are redacted; logs still contain machine paths and
command diagnostics, so inspect them before sharing.

```sh
# Repeat the SAME selection and desired inputs with the printed run ID:
bash scripts/setup.sh --yes --only directories,git --with-deps --resume RUN_ID
```

Resume validates host, user, home, profile, runner version, selection and desired
inputs, then creates a linked journal and re-probes everything. It never trusts an
old success record to skip a probe. A fresh invocation also re-probes and can recover
interrupted file transactions. Input changes require a fresh run. A common apply
lock prevents cooperating setup processes from mutating the host concurrently.

## Configuration and compatibility

Existing launchers, checkouts, SSH settings, apt sources, nvm defaults, profiles,
and authorized keys are preserved. There is no automatic git pull, reset or clean.
SSH policy is checked using `sshd -T`. A conflicting `99-local.conf` produces a
manual result and leaves the file intact. Other steps continue. New SSH settings
are validated and reloaded before committing; failure restores the prior file.
Proxmox keeps root key access and requires a recorded successful operator key login
before disabling passwords. It preserves the cluster authorized-keys symlink.

Managed file changes stage and fsync contents, publish atomically, and keep a private
adjacent `.nas-setup-recovery` record until commit. A retry reconciles an interrupted
write only if the current file matches the recorded before/after contents. An
intervening operator edit is preserved and reported for manual review. Routing
changes preserve other sysctl settings and existing advertised subnets. A failed
routing apply restores the prior file and live forwarding value where possible.

Install/login/integration tasks are separate. Installed Tailscale is not reinstalled
because it needs login. An unavailable Node blocks Pi without blocking unrelated
CLIs. External installers are downloaded completely before execution; only downloads
have bounded retries. Package/database writers are not blindly retried or forcibly
killed during cancellation. Vendor installers and package scripts remain trusted
code; the entire run is not a transaction.

Power recovery is optional at the prompt. Local IPMI and supported Mac autorestart
settings are read back after changes; ordinary desktop firmware receives manual
instructions. VMs are not applicable. See [power recovery](power-restore.md).

## Distribution and updates

The shell entrypoints are generated from `scripts/setup-launcher.sh.in`. From a
checkout they execute the local modules. Outside a checkout they download a complete,
version-pinned `setup.pyz` and SHA-256 checksum. Checksums detect incomplete/mismatched
assets; they do not independently authenticate the publisher.

**Release prerequisite:** the remote wrappers in this change require the
`setup-v1.0.0` GitHub release. Use a checkout until that release is published. The
release workflow validates the tag against `setup_core.VERSION`, runs setup tests
and the disposable Debian integration, and publishes both assets together. Publishing
and real Proxmox/desktop validation remain release operations, not effects of opening
a PR. Coordinate release availability before advertising the new raw-script URLs.

`setup-command` installs a complete content-addressed local bundle and a launcher only
when its path is empty. Existing commands remain untouched. Updates are explicit:
run the desired checkout/release directly; inspect and move aside an old generated
launcher if you want `--only setup-command` to create a new one. Old bundles remain
available for rollback. The wrapper offers Python installation only during apply:
Debian apt or an existing Homebrew. Proxmox never replaces its system interpreter.

## Maintenance and validation

Register tasks and capabilities in `setup_core/profiles.py`; implement cohesive
`probe(ctx, task)` and `apply(ctx, task)` functions under `setup_tasks`. The runner
uses another probe as verification after `changed`. Tasks do not call other tasks,
prompt independently, source shell profiles into the controller, or swallow failures.
Use `ctx.run` argument arrays, typed outcomes, `Change` transactions for managed
configuration, and fixture-only command boundaries in tests. nvm has one small shell
adapter with its own strict mode.

```sh
python3 -m pytest
ruff check setup_core setup_tasks scripts/build_setup.py tests/test_setup_runner.py tests/test_setup_tasks.py tests/test_setup_distribution.py
python3 scripts/build_setup.py --check
bash tests/integration/debian.sh  # mutates only a disposable Docker container
```

CI runs the full suite on Linux, setup tests on macOS, and real apt/systemd repeat-run
and dependency checks in Debian 13. The macOS suite uses fixtures for power settings;
it does not modify the runner's firmware. No production host installation, power
cycle, live account authentication, or Proxmox VM test is implied by unit-test success.
See the [preservation audit](setup-safety.md) and [original plan](setup-maintenance-plan.md).
