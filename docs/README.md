# New Machine Setup

Setting up a Debian 13 machine from scratch, in order. Each step links to the details.
Host-specific facts (IPs, MACs, users) live in the root [README](../README.md#local-machines),
not here.

Proxmox VM or bare metal? Both follow the same path. Steps marked **(VM)** only apply to a
Proxmox guest.

## Or run the setup script

After the Debian install in [01-debian-install.md](01-debian-install.md), which is still
manual (VM creation, the installer itself), one command does steps 2-7 on Debian 13 or macOS:

```
curl -fsSL https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/setup.sh | bash
```

It clones this repo to `~/tools/nas_scripts` and links it as `~/.local/bin/setup-machine`.
Rerun it any time; steps already done are skipped:

```
setup-machine            # offer each step that is not done yet
setup-machine --status   # just show what is done and what is left
```

It ends by listing the sign-ins it can't do for you (`gh auth login`, `codex`, `pi`,
`claude`). The steps below are what the script does, for doing it by hand or fixing one step.

| # | Step | Doc |
|---|------|-----|
| 1 | Create the VM **(VM)**, install Debian + XFCE, fix sudo | [01-debian-install.md](01-debian-install.md) |
| 2 | Update, guest agent **(VM)**, disable sleep, snapshot **(VM)** | [02-post-install.md](02-post-install.md) |
| 3 | SSH server, keys, hardening | [03-ssh.md](03-ssh.md) |
| 4 | Tailscale + iPhone access | [04-tailscale.md](04-tailscale.md) |
| 5 | git, gh, Node 22 via nvm | [05-dev-tools.md](05-dev-tools.md) |
| 6 | Codex, Pi, Claude Code, FirstMate | [06-agent-clis.md](06-agent-clis.md) |
| 7 | Herdr for persistent agent sessions | [07-herdr.md](07-herdr.md) |
| - | Optional: pass the GT 1030 through to the VM **(VM)** | [gpu-passthrough.md](gpu-passthrough.md) |

When you're done, add the machine to [Local Machines](../README.md#local-machines).

## Done checklist

- [ ] `cat /etc/os-release` shows Debian 13 (trixie)
- [ ] `sudo whoami` prints `root`
- [ ] Sleep targets masked
- [ ] Key-based SSH works; password auth refused
- [ ] `tailscale status` shows the machine; SSH works from the iPhone on cellular
- [ ] `gh auth status` is logged in; `node --version` is 22.19.0 or newer
- [ ] `codex`, `pi` and `claude` start and are signed in
- [ ] `herdr` detaches and reattaches with panes still running
- [ ] **(VM)** Snapshot `clean-xfce-base` taken
