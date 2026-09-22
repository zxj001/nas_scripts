# setup.sh repeat-run and data preservation audit

Scope: all 21 registered steps and the runner in `scripts/setup.sh`. This review
does not cover `proxmox_setup.sh` or recursively audit every external installer and
package maintainer script. Tests use isolated homes and stub system operations;
they are not a fresh Debian VM installation test.

## Findings and fixes

| Area | Finding | Result |
| --- | --- | --- |
| HTTPS prerequisites | Tailscale ran before dev-tools installed curl. Other `--only` download steps had the same dependency. | Download steps ensure curl and CA certificates on Debian. |
| Installer downloads | Streaming to a shell could execute a partial failed download. | Download to a temporary file, check success, execute, remove the temporary file. |
| Status / arguments | `--status` and invalid step selections could pull, clone, or replace a launcher. | Validate selections before self-update; status skips self-update entirely. |
| Checkout | A normal invocation pulled even when the checkout had local changes or another branch/origin. | Skip automatic update for those cases. Clean main checkouts of the expected HTTPS origin retain fast-forward updates. |
| Launcher | `ln -sf` replaced existing files or links. | Preserve occupied launcher paths, including directories and dangling links. Reuse the expected link. |
| SSH config | File existence counted as hardening; the writer could truncate a conflicting file and remove it on validation failure. | Check effective global policy with sshd -T. Preserve existing files and report skipped hardening if policy differs, then continue setup. Validate new files and roll back only a file created by this invocation on failure. |
| Apt config | GitHub key/source and GPU source writes could truncate user configuration. | Stage new files, publish without replacing existing paths, preserve an existing GitHub keyring, refuse conflicting source files. Include the existing GPU drop-in when checking components. |
| nvm | Reinstalling nvm can overwrite its checkout; setup reset `NVM_DIR` and the default alias. | Reuse existing nvm, honor `NVM_DIR`, preserve an existing default alias, and refuse an incomplete occupied installation directory. |
| Profiles / keys | Repeated appends could duplicate lines; appends could join an unterminated last line. | Deduplicate exact lines and separate appended content with a newline. |
| Tailscale | Installed but signed-out clients reran the installer. | Install only when absent, then connect. Keep existing flags/state; do not use reset. |
| Package upgrade | Full upgrade could remove packages or ask to replace modified dpkg config files. | Keep existing dpkg conffiles and abort if the upgrade requires package removal. |
| Herdr integration | Hook setup could edit existing Claude settings. | Preserve an existing settings file or link and print the integration command for manual use. |
| Runner checks | The initial todo list became stale after earlier steps changed tool availability. | Recheck each step immediately before offering/running it. |
| Platform | Every Linux distribution was treated as Debian despite hardcoded trixie apt sources. | Reject Linux distributions other than Debian 13 before setup work. |

## Every step reviewed

| Step | Repeat-run behavior and remaining considerations |
| --- | --- |
| brew | Discover standard Homebrew paths before checks. Skip an existing installation; append the shell initialization line once. First installation delegates to Homebrew. |
| sudo | Check both sudo availability and group membership. `usermod -aG` retains other groups. Stop for a new login after adding membership. |
| upgrade | Offer when the local apt simulation reports installs. Preserve conffiles; refuse package removals. Package lists can be stale and upgrades intentionally change installed versions. |
| guest-agent | Skip when qemu-guest-agent is enabled. Package install and service enable are repeatable; the check does not separately verify spice-vdagent. |
| no-sleep | Check all four sleep targets. Mask without force, so existing custom units are not overwritten. |
| power-restore | Optional IPMI/macOS power-failure restart setting with readback verification. Desktop BIOS/UEFI setup remains manual/unverified; VMs are n/a. No shutdown/reboot is issued. |
| ssh | Skip an active SSH service. Package install/enable does not directly rewrite sshd configuration. |
| ssh-keys | Preserve existing authorized keys, validate pasted keys, avoid exact duplicate lines, and enforce SSH directory/file modes. Equivalent keys with different comments are not deduplicated. |
| ssh-harden | Require a non-root user with a valid authorized key before writing. Preserve existing config and continue with an explicit skipped-hardening message when effective policy differs. Validate global policy and roll back only newly created config on failure. |
| tailscale | Ensure HTTPS dependencies before first installation; reconnect an installed client without reinstalling it. Sign-in may still be interactive. |
| dev-tools | Check curl, build tools, CA certificates, git, jq, rg, Python 3 with pip and venv, and directories on Debian. Install missing packages and create directories without deleting existing contents. |
| gh | Skip an available CLI. Preserve existing apt keyring/source configuration; download a new key fully before publication. |
| node | Skip a sufficiently new active Node. Reuse nvm, preserve custom directories/defaults, and install Node 22 without deleting older versions. A deliberately older default can cause this step to be offered again. |
| codex | Skip an available CLI. On first installation, use the vendor installer or Homebrew. |
| pi | Skip an available CLI. Use npm without lifecycle scripts; the global install location follows the active Node/npm configuration. |
| claude | Skip an available CLI. First installation delegates to the vendor/Homebrew; append local-bin PATH once on Debian. |
| herdr | Skip an available CLI. First installation delegates to the vendor; preserve existing Claude hook settings. |
| firstmate | Recognize ordinary clones and worktrees (`.git` can be a file). Refuse an occupied non-checkout destination. Never reset or clean a checkout. |
| shellfish | Debian only. `manual` until the iPhone app has written `~/.shellfishrc`; never edits that file. Installs openssl/xxd/curl/cron only if one is missing. Adds the widget cron entry once, keeps other entries, and refuses to write when the crontab cannot be read. |
| gpu | Skip a working NVIDIA driver. Preserve apt sources, add only missing components, then install packages. Existing source detection is conservative text matching, not a full deb822 parser. |

## Practical limits

- This is repeatable setup, not an immutable machine image: selected upgrades,
  authentication, package installation, service enable/mask operations, and a clean
  checkout's fast-forward update intentionally change state.
- Conflicting apt configuration requires manual reconciliation and stops the step.
  Existing SSH configuration is preserved; setup explicitly skips hardening and
  continues when the effective global policy differs. Neither case silently claims
  the desired configuration is done.
- SSH checks use `sshd -T` for effective global policy and `sshd -t` for syntax.
  They do not evaluate every user/address-specific `Match` block. Verify a new key-based session and
  effective SSH settings before closing the current session. Public-key presence
  alone does not prove access works.
- External installers, package maintainer scripts, sourced nvm configuration, and
  authentication clients remain trusted dependencies. Completing a download before
  execution prevents partial execution, but does not make vendor code transactional
  or establish a blanket guarantee about files it may modify.
- A failed first nvm install may leave an incomplete directory. Setup preserves it
  and stops; inspect and repair it before retrying. Config writes are atomic, but
  the whole setup run is not a transaction or a concurrent-run coordinator.

## Validation

`tests/test_setup_safety.py` covers absent curl, Tailscale reconnect/repeat runs,
failed downloads, existing files/directories/symlinks, unchanged config inodes and
timestamps, launcher preservation, read-only status, customized checkouts,
profile appends, nvm directory/default preservation, Firstmate worktrees, SSH
rollback, GitHub/GPU configuration conflicts, partial sleep masks, and stale
runner checks. `tests/test_setup.sh` also checks syntax and ShellCheck.

Implementation references: [nvm's pinned installer](https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.7/install.sh)
performs a forced checkout when rerun on its repository;
[Debian's apt-get manual](https://manpages.debian.org/trixie/apt/apt-get.8.en.html)
documents `--no-remove` as aborting operations that would remove packages.
