# Maintainable setup with failure isolation

Original approved design (implementation now described in [setup-runner.md](setup-runner.md)). Baseline: commit `3f4cc91`, `scripts/setup.sh`
(806 lines, 20 steps) and `scripts/proxmox_setup.sh` (409 lines, 4 steps).

## Desired behavior

A setup run attempts every selected task whose actual prerequisites are available.
It displays warnings and failures when they occur, then continues independent work.
It skips dependent tasks with a concrete reason. It finishes with a truthful summary
and a retry command, even when some tasks failed. An interrupted run can be resumed
without replacing user files or trusting stale success markers.

For example: Node installation fails; Pi is blocked because a compatible Node/npm
is unavailable; GitHub CLI, SSH, and power recovery still run. If suitable Node/npm
was already installed, Pi can run despite a failed optional Node update. A custom
SSH configuration generates a visible manual-action result, not a fake success,
and does not block unrelated installation.

## Why another patch to the current loop is insufficient

- Both scripts duplicate argument parsing, selection, prompts, status, and execution.
- Their ERR traps end the process on the first failing step.
- Dependency ordering lives in the STEPS arrays and assumptions inside functions.
  This caused the curl-before-Tailscale problem; ordering alone does not handle skips.
- nvm and Homebrew change the runner's environment. Isolating steps without replacing
  this implicit communication would break subsequent tasks.
- Return codes conflate absent, failed-to-check, manual, and pending-login states.
  A logged warning followed by return 0 can lose the warning in the final result.
- Tests slice source definitions and override shell functions. They do not exercise
  all real command, systemd, packaging, reboot, or hardware behavior. The accidental
  nvm installation during earlier testing illustrates why test isolation must be
  enforced, not just intended.

Simply adding `|| true` or calling existing shell functions in an `if` is not the
solution: it can change errexit behavior inside the function and let later commands
run after an earlier command failed. Failure isolation belongs at a process boundary.

## Recommended architecture

Use a small **Python 3.11+ standard-library runner**, separate task modules, and
short Bash adapters only where a shell is necessary (notably nvm). Keep the two
existing shell entrypoints as thin bootstrap/compatibility wrappers. No runtime pip
dependencies, database, daemon, plugin discovery, or parallel installers.

| Component | Responsibility |
| --- | --- |
| scripts/setup.sh, scripts/proxmox_setup.sh | Detect runtime, load a complete versioned bundle, select the correct profile, delegate. No installation business logic. |
| setup_core/cli.py | Existing flags, proposed plan/resume controls, input validation and interactive choices. |
| setup_core/runner.py | Schedule serially, isolate a task worker, classify results, continue, and summarize. |
| setup_core/model.py | Explicit task, capability, diagnostic and result types; validate identifiers and dependency cycles. |
| setup_core/commands.py | Argument-array subprocesses, explicit environment, streaming output, return codes, timeout/cancellation handling. |
| setup_core/files.py | Preserve existing files, stage managed writes, backup/restore, atomic publication, permissions. |
| setup_core/state.py | Run journal, restricted log storage, interruption and resume bookkeeping. |
| setup_core/profiles.py | Debian, macOS and Proxmox task selections and policy differences. |
| setup_tasks/*.py | One cohesive feature per module: probe, apply and verify. Profiles share mechanics, not incompatible SSH policy. |
| setup_adapters/*.sh | Small shell-only operations executed as fresh processes; never sourced into the orchestrator. |

Aim for entrypoints under 100 lines, cohesive task modules usually under 150–200
lines, and runner components that can be read independently. These are review guides,
not line-count tests. Deleting duplicate runners and implicit contracts matters more
than reducing the total number of lines.

A task declares its id, applicable profiles, defaults, required capabilities,
provided capabilities, interactive needs, affected resources, and probe/apply/verify
functions. These are plain code/data structures, not a new DSL. Tasks do not call
each other, prompt outside the central UI, or modify the parent process environment.

Workers return a validated JSON result through a separate result file/descriptor;
stdout/stderr remain logs. The controller validates task id, schema and return code.
A worker crash, missing result or malformed result is a failure, never success.
Expected and unexpected exceptions become task failures with diagnostics. Shell
adapters run in a new Bash process with their own strict mode.

Explicit context includes the target user/home, platform, resource paths, executable
locations and approved environment values. Discover Node/npm and Homebrew paths after
installation and pass them to consumers; do not depend on exported shell functions,
source arbitrary profiles, or change the user's default Node version.

Python's [subprocess documentation](https://docs.python.org/3/library/subprocess.html)
provides the process, argument-array and environment controls needed here. This is
an orchestration migration; existing package/service commands remain incremental
adapters rather than being rewritten all at once.

### Runtime and distribution are part of the plan

Python is an added bootstrap dependency, particularly on macOS and minimal Debian.
Use an already supported interpreter first. Otherwise the apply-mode bootstrap
explains and offers the minimum runtime installation: apt on Debian, supported
Homebrew Python on macOS. Proxmox uses its existing interpreter if compatible;
never replace its system interpreter or install unrelated host packages implicitly.
If runtime installation is unavailable, stop clearly at bootstrap and show the
manual prerequisite. A local/offline bundle remains usable once Python is present.

Preserve current entrypoint URLs and CLI flags. Release CI builds one self-contained
Python zipapp/bundle from the modular sources; fetch a complete pinned release, verify
its release checksum, then execute it. A same-channel checksum detects damage and
version mismatch, not compromise of the publisher. Never fetch task modules separately
or mix versions. A local checkout runs local code. Remove implicit git pull/re-exec;
make updates an explicit operation and keep a previous complete bundle for rollback.

`--help`, `--status` and the proposed `--plan` must not install a runtime, update a
checkout, refresh apt, write profiles, or prompt for sudo. If Python is absent, the
wrapper reports that limitation rather than pretending it checked every task.

## Outcome and diagnostic contract

Warnings are diagnostics, separate from completion status. A verified task may
complete with a warning. A missing required configuration is manual/blocked, not
completed-with-warning. Unexpected nonzero command exits are failed operations,
unless the adapter explicitly handles that documented exit code.

| Outcome | What it means | Continue? |
| --- | --- | --- |
| satisfied | Probe verified the desired state; no write needed. | Yes. |
| changed | Apply and post-check verified the desired state. | Yes. |
| failed | An attempted operation or its post-check failed. | Yes, outside affected dependencies/resources. |
| blocked | A required capability is absent or unsafe; name its cause. | Yes, for other tasks. |
| manual | A selected outcome needs firmware changes or user reconciliation. | Yes; dependent capability remains unmet. |
| deferred | Re-login, authentication, or reboot is required. | Yes, where those capabilities are unnecessary. |
| skipped | The user deliberately declined the task. | Yes; dependents still probe actual prerequisites. |
| not-applicable | This profile/hardware cannot perform the task. | Yes. |

Display a short message and remediation immediately. Stream relevant command output;
keep private raw logs separately. Always finish with a table of task, outcome,
warnings, reason and next action. Include cause chains such as `pi blocked: node >=
22.19 unavailable; node.install failed downloading archive`.

Default is continue. Offer `--fail-fast` for debugging. Apply-mode exit codes:
0 when selected, non-declined outcomes are satisfied and only advisory warnings
remain; 1 after processing all possible work when selected tasks failed, were
blocked, require manual work, or are deferred; 2 for invalid input or a fatal engine
error; 130 for user cancellation. Read-only status can exit 0 with an accurate table
unless the inspection itself fails. Preserve structured outcomes in JSON for callers.

Stop the whole run only when it cannot operate safely: invalid/cyclic plan, unusable
bootstrap/runtime, another active run, broken result/state infrastructure, explicit
cancellation, or evidence of uncontrolled modification that cannot be scoped. Do not
interpret ordinary lack of sudo as universally fatal: block privileged tasks and
continue eligible user tasks. Likewise, defer sudo-group-dependent work after a
re-login request while independent work continues.

## Dependencies: capabilities, not the old array order

Probe the requested desired outcome first. If already satisfied, it does not need
installation prerequisites. Otherwise probe its capabilities, plan known providers,
and execute a stable topological order. Validate cycles before any writes. Re-probe
capabilities after provider completion or failure: a failed grouped task does not
necessarily remove an already usable capability. If a provider fails midway and leaves
a resource uncertain, block tasks using that resource until a health probe succeeds.

| Task | Actual prerequisites | Failure must not block |
| --- | --- | --- |
| tailscale.install | HTTPS download and appropriate package/privilege capabilities | Node, git clone, unrelated local configuration if their own requirements hold. |
| tailscale.login | Tailscale installed, usable service/network, operator authentication | Other package installs. |
| subnet-router | Tailscale configured, forwarding capability and privilege; approval may remain manual | SSH and unrelated host tasks. |
| node.install | Download capability, chosen install directory; brew only on a brew-backed path | SSH, gh and other non-Node tools. |
| pi.install | A compatible Node and npm at explicit paths | Other CLIs that do not need Node. |
| ssh.harden | SSH service/config access, valid operator key, profile-specific login evidence | Tailscale, Node and power recovery. |
| firstmate.clone | git, repository access, available destination | System updates and SSH changes. |
| power-restore | Detected local backend, then its tool/privilege capability; firmware may remain manual | Everything unrelated. |

Split broad dev-tools into reusable capability providers (download tools, git, build
tools) with a compatibility group alias. Likewise separate install from sign-in,
SSH keys from hardening, and Herdr install from Claude hook configuration. Preserve
Proxmox's stricter proven-root-key requirement and hypervisor package restrictions.
A full system upgrade is optional work, never a universal prerequisite.

Keep `--only` literal: without new flags, inspect outside prerequisites but do not
silently install them. Report missing prerequisites as blocked and give the exact
expanded command. Add opt-in `--with-deps` to include minimal providers; show the
expanded plan before execution. `--yes` accepts that plan, not arbitrary extra tasks.
If a user declines a provider and the capability is absent, show blocked dependents.

## Safe failure, retry and resume

- Each task has probe → apply → verify. Command exit 0 alone does not establish success.
- Group file write, syntax check and service reload as one operation with rollback.
  Preserve operator files and symlinks. Explicitly skipped SSH configuration remains
  manual and appears in the final report, while the run continues.
- Record affected resources such as apt/dpkg, sshd config or a particular checkout.
  Failed rollback blocks consumers of that resource. Continue unrelated work; abort
  globally only if the unsafe scope cannot be determined.
- Record a run id, profile/version, target identity, desired-input digest, task/phase,
  timestamps, diagnostic and log path. Restrict permissions; redact credentials and
  sign-in tokens in shareable summaries. Validate existing state paths and use atomic
  writes. Acquire a machine-level apply lock across users and both entrypoints.
- An interrupted worker is incomplete/unknown, not successful. On `--resume` re-probe
  actual state and execute only missing operations. Journals are evidence, not truth.
  Reconcile plan/version changes before resuming; never replay blindly.
- Retry read-only network probes/downloads with short bounded backoff. Do not blindly
  retry package transactions, config changes, authentication or service reloads.
  Re-probe and either repair a known owned partial artifact or give manual recovery.
- Cancellation stops new tasks, signals the active process group and records state.
  Package operations need adapter-specific interruption/recovery; do not universally
  kill dpkg after a short timeout and then continue other package changes.
- Interactive authentication receives the terminal, outside captured private logs.
  Noninteractive runs return deferred/manual with instructions instead of hanging.

## Delivery sequence: five reviewable PRs

| PR | Change | Acceptance gate |
| --- | --- | --- |
| 1. Contract and baseline | Capture each current task, profile rule and dependency; introduce typed outcomes, pure planning and fixture scenarios. Keep entrypoint behavior unchanged. | Cycle rejection, prerequisite checks, warning/manual distinction and deterministic summary tests. |
| 2. Shared runner and bootstrap | Add the minimal Python runtime path, isolated workers, continue-by-default scheduling and streaming/final reporting. Extract importable shell task definitions for temporary adapters; no source slicing. Expose an opt-in new engine. | Node failure blocks Pi but independent tasks complete; warning/manual SSH does not stop Tailscale; worker crash/malformed result is visible. Existing CLI/--status compatibility verified. |
| 3. Small modules and capabilities | Migrate simple tasks first, then downloads/Node/auth, then SSH and Proxmox. Remove global environment communication and duplicate runner logic as each profile migrates. | Every migrated feature has probe/apply/verify tests, rerun tests, explicit capability requirements and unchanged profile safety constraints. |
| 4. Durable recovery and file operations | Consolidate file updates, rollback, resource health, logs, apply lock, resume and safe retry. | Interrupted write/reload, failed rollback, shared apt failure, re-login, cancellation and repeat-run tests. All user fixture data remains unchanged outside authorized edits. |
| 5. Real environments and cutover | Test packaged bootstrap on clean Debian 13; test macOS discovery/adapters; test disposable Proxmox VM before its profile cutover. Publish a versioned bundle and switch wrappers. Delete transitional legacy runners. | Fresh run, second run, partial failure, resume and --only pass through public CLI; release bundle runs from outside the checkout. Rollback uses previous complete release. |

Start real-environment jobs in PR 2 and expand them through cutover; do not wait
until PR 5 to discover bootstrap/runtime problems. Roll out profile by profile so
untested Proxmox changes cannot ship simply because Debian passes.

## Validation that protects the machine running tests

Use pure unit tests for planning/results, injected command/filesystem interfaces for
task logic, and controlled fake executables for subprocess contract tests. Give tests
isolated HOME, NVM_DIR, PATH and state directories. Fail closed on unexpected commands;
do not inherit user profiles or provide unrestricted sudo. OS-mutating integration
tests run only in disposable VMs/containers with fixture storage and explicit network
access. Containers cover package/bootstrap paths; they do not prove systemd, reboot,
SSH lockout prevention, or physical power recovery.

Required scenarios: one failure at each task/phase; missing and preexisting dependencies;
provider declined; warning with verified capability; manual missing capability; blocked
transitive dependents; independent continuation; check failure distinct from absent;
duplicate/cyclic metadata; twice-run no-op; interrupted download/write/reload; failed
rollback; cached sudo absent; new group/login deferred; user cancellation; result corruption;
secret redaction; missing Python; complete-bundle bootstrap; all compatibility flags.
Keep ShellCheck for remaining shell adapters. Require the real Debian gate before
making the new runner default. Validate power-loss behavior separately on representative
hardware; never fake this coverage with command stubs.

## Decisions and tradeoffs

Recommendation: Python stdlib orchestration with incremental migration. It introduces
runtime bootstrapping but makes dependency planning, results and tests explicit.
Keeping Bash throughout avoids that dependency, but still needs disciplined process
isolation and a result protocol and is harder to extend for typed state/resume.
Ansible could suit centrally managed fleets later; introducing its controller/runtime
and interaction model is unnecessary for these local setup entrypoints today.

Do not add a general plugin framework, broad configuration language, parallel execution,
Windows implementation, unattended firmware writes, or a fleet-management service in
this refactor. These would expand the maintenance burden before solving current failure
handling. The first useful deliverable is PR 2's continuation runner, backed by PR 1's
contract tests; the final goal is removal of the duplicate monolithic implementations.

## Implementation status

The shared Python runner, task modules, generated entrypoints, literal selection and
capabilities, typed outcomes, continuation, journaling/re-probed resume, managed file
recovery, and complete bundle packaging are implemented together. Transitional shell
adapters were unnecessary except for nvm; the old duplicated runners and source-slicing
tests have been removed. Linux and macOS contract jobs plus disposable Debian package/
service integration are in CI. Release publication and actual Proxmox VM/firmware/login
validation remain rollout gates; they have not been represented as completed by mocks.
