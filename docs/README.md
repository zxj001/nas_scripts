# New Machine Setup

Setting up a Debian 13 machine from scratch, in order. Each step links to the details.
Host-specific facts (IPs, MACs, users) live in the root [README](../README.md#local-machines),
not here.

Proxmox VM or bare metal? Both follow the same path. Steps marked **(VM)** only apply to a
Proxmox guest.

## Or run the setup script

After the Debian install in [01-debian-install.md](01-debian-install.md), which is still
manual (VM creation, the installer itself), one command does steps 2-8 on Debian 13 or macOS:

Run from a checkout (Python 3.11+; the apply wrapper offers a runtime installation
on supported machines when needed):

```sh
bash scripts/setup.sh
bash scripts/setup.sh --status
bash scripts/setup.sh --only tailscale --with-deps
```

Each task probes current state, applies missing work, and verifies the result.
Failures and warnings remain visible in the final summary; independent tasks continue.
`--only` selects exactly the named tasks/groups. Add `--with-deps` to also select
prerequisite providers, including curl and CA certificates on a fresh Debian install.
`--yes` accepts selected changes but defers sign-ins and missing input.

The `setup-command` task installs `~/.local/bin/setup-machine` with a complete local
bundle, preserving any existing command. Reruns preserve user files and checkouts;
updates are explicit. Existing SSH configuration that needs manual hardening is
reported without replacing the file or stopping other work.

```sh
setup-machine --plan
setup-machine --status
setup-machine --only power-restore
```

Power recovery supports local IPMI and compatible Mac settings; ordinary desktops
receive BIOS/UEFI instructions. See [power recovery](power-restore.md).
For resume, outcomes, architecture, release/bootstrap prerequisites and testing, see
[the setup runner](setup-runner.md) and [preservation audit](setup-safety.md).

Remote wrappers download the pinned `setup-v1.0.0` bundle. Until that release is
published, use a checkout. Once available:

```sh
curl -fsSL https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/setup.sh | bash
```

A minimal Debian install needs `curl` and `ca-certificates` to run that download
command. Local checkout setup installs them through its dependency provider.
The steps below describe the corresponding manual operations.

`--only a,b` runs just those tasks. It names every prerequisite of a selected task
that is not available yet (for example `node` for `pi`) and, when run from a terminal,
asks whether to add each one. `--with-deps` adds them all without asking; with `--yes`
and no `--with-deps`, they are only named.

For the ShellFish Home Screen widget (name, CPU, temperature, memory, disk), install Shell
Integration from the iPhone app first, then run `--only shellfish`. See
[ShellFish widgets](08-shellfish-widgets.md).

| # | Step | Doc |
|---|------|-----|
| 1 | Create the VM **(VM)**, install Debian + XFCE, fix sudo | [01-debian-install.md](01-debian-install.md) |
| 2 | Update, guest agent **(VM)**, disable sleep, snapshot **(VM)** | [02-post-install.md](02-post-install.md) |
| 3 | SSH server, keys, hardening | [03-ssh.md](03-ssh.md) |
| 4 | Tailscale + iPhone access | [04-tailscale.md](04-tailscale.md) |
| 5 | git, Python + pip, gh, Node 22 via nvm, Docker, Chromium | [05-dev-tools.md](05-dev-tools.md) |
| 6 | Codex, Pi, Claude Code, FirstMate | [06-agent-clis.md](06-agent-clis.md) |
| 7 | Herdr for persistent agent sessions | [07-herdr.md](07-herdr.md) |
| 8 | ShellFish widget: CPU, temp, memory, disk on the iPhone | [08-shellfish-widgets.md](08-shellfish-widgets.md) |
| - | Optional: pass the GT 1030 through to the VM **(VM)** | [gpu-passthrough.md](gpu-passthrough.md) |
| - | SSH and Tailscale on the Proxmox host itself **(host)** | [pve-host.md](pve-host.md) |

When you're done, add the machine to [Local Machines](../README.md#local-machines).

## Done checklist

- [ ] `cat /etc/os-release` shows Debian 13 (trixie)
- [ ] `sudo whoami` prints `root`
- [ ] Sleep targets masked
- [ ] Key-based SSH works; password auth refused
- [ ] `tailscale status` shows the machine; SSH works from the iPhone on cellular
- [ ] `gh auth status` is logged in; `node --version` is 22.19.0 or newer
- [ ] `python3 -m pip --version` works and `python3 -m venv` creates a venv
- [ ] `docker compose version` and `docker run --rm hello-world` work without sudo
- [ ] `chromium --version` works (macOS: Google Chrome or Chromium is installed)
- [ ] `codex`, `pi` and `claude` start and are signed in
- [ ] `herdr` detaches and reattaches with panes still running
- [ ] ShellFish widget on the iPhone shows CPU, Temp, Mem and Disk
- [ ] **(VM)** Snapshot `clean-xfce-base` taken
