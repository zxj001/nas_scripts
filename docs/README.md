# New Machine Setup

Setting up a Debian 13 machine from scratch, in order. Each step links to the details.
Host-specific facts (IPs, MACs, users) live in the root [README](../README.md#local-machines),
not here.

Proxmox VM or bare metal? Both follow the same path. Steps marked **(VM)** only apply to a
Proxmox guest.

## Or run the setup script

After the Debian install in [01-debian-install.md](01-debian-install.md), which is still
manual (VM creation, the installer itself), one command does steps 2-8 on Debian 13 or macOS:

```
curl -fsSL https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/setup.sh | bash
```

It clones this repo to `~/tools/nas_scripts` and links it as `~/.local/bin/setup-machine`.
On a box without git (a fresh Debian 13 install) it does that as soon as the dev-tools
step has installed git. Rerun it any time; steps already done are skipped:

```
setup-machine            # offer each step that is not done yet
setup-machine --status   # just show what is done and what is left
```

`--only a,b` runs just those steps. Any prerequisite that is not done yet (for example
`node` for `pi`, or `ssh` and `dev-tools` for `shellfish`) is named and added, and you
are asked about it like any other step. `--status --only a,b` lists them without asking.

On a minimal Debian install, install the download prerequisite first:

```
sudo apt-get update
sudo apt-get install -y curl ca-certificates
```

When running a local copy of `scripts/setup.sh`, its download steps install these
prerequisites themselves, including `--only tailscale`.

`--status` does not update or clone the repository. Normal runs preserve modified
checkouts, existing launchers, custom apt/SSH files, and nvm defaults. Conflicting
apt configuration stops the affected step with the path to resolve; it is not replaced.
Existing SSH configuration is checked through `sshd -T`: if it still needs hardening,
setup reports that hardening was skipped and continues without replacing the file.
See the [setup safety audit](setup-safety.md) for the step-by-step review and limits.

For the ShellFish Home Screen widget (CPU, temperature, memory, disk), install Shell
Integration from the iPhone app first, then run `setup-machine --only shellfish`. See
[ShellFish widgets](08-shellfish-widgets.md).

For automatic startup after a power outage, run `setup-machine --only power-restore`.
It configures supported local IPMI/macOS settings and provides BIOS/UEFI guidance
for desktops without IPMI. `manual` means firmware setup remains unverified. See
[power recovery](power-restore.md).

It ends by listing the sign-ins it can't do for you (`gh auth login`, `codex`, `pi`,
`claude`, and on macOS Tailscale.app: open it and sign in). The steps below are what the
script does, for doing it by hand or fixing one step.

| # | Step | Doc |
|---|------|-----|
| 1 | Create the VM **(VM)**, install Debian + XFCE, fix sudo | [01-debian-install.md](01-debian-install.md) |
| 2 | Update, guest agent **(VM)**, disable sleep, snapshot **(VM)** | [02-post-install.md](02-post-install.md) |
| 3 | SSH server, keys, hardening | [03-ssh.md](03-ssh.md) |
| 4 | Tailscale + iPhone access | [04-tailscale.md](04-tailscale.md) |
| 5 | git, Python + pip, gh, Node 22 via nvm | [05-dev-tools.md](05-dev-tools.md) |
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
- [ ] `codex`, `pi` and `claude` start and are signed in
- [ ] `herdr` detaches and reattaches with panes still running
- [ ] ShellFish widget on the iPhone shows CPU, Temp, Mem and Disk
- [ ] **(VM)** Snapshot `clean-xfce-base` taken
