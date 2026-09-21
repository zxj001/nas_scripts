# Proxmox host: SSH and Tailscale

Automated by `scripts/proxmox_setup.sh`. As root on the host:

```
curl -fsSL https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/proxmox_setup.sh | bash
```

It offers `ssh-keys`, `ssh-harden`, `tailscale` and `subnet-router` (off by default), with
the same `--status`, `--yes` and `--only a,b` flags as `setup-machine`. Rerun the line to
update. It refuses to run anywhere but a Proxmox VE host.

Host-side, on `pve1` itself (192.168.1.203, web UI :8006). The numbered steps cover a
Debian guest VM or workstation; this page covers the Proxmox VE host under them.

## Why the host is different

Proxmox VE is Debian underneath, but it is not a normal Debian box:

- The web UI's shell, migration and clustering log in as **root over SSH** between nodes.
- So do **not** run `setup-machine` here (use `proxmox_setup.sh` above), and do **not** set `PermitRootLogin no`.
- The right hardening is root by key only: `PermitRootLogin prohibit-password` plus
  `PasswordAuthentication no`.

Check which Debian the host is on (PVE 8 is Debian 12, PVE 9 is Debian 13):

```
pveversion
```

## SSH

sshd already runs on the host, and root can log in with the password set during install.

From your client (make a key first with `ssh-keygen -t ed25519 -a 100` if you have none):

```
ssh-copy-id root@192.168.1.203
ssh root@192.168.1.203           # must log in without a password prompt
```

Only once key login works, and with that session still open, on the host:

```
cat > /etc/ssh/sshd_config.d/99-local.conf <<'CONF'
PermitRootLogin prohibit-password
PasswordAuthentication no
CONF
sshd -t                          # no output = valid
systemctl reload ssh
```

Confirm a **new** key-based session works before closing the old one. If you do lock
yourself out, the IPMI console at 192.168.1.118 still gets you a root shell (see the
root [README](../README.md#ipmi-supermicro-out-of-band)).

## Tailscale

The standard installer supports the host's Debian release:

```
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up                     # open the printed URL to authenticate
tailscale status
tailscale ip -4                  # the 100.x.x.x address
```

Record the Tailscale IP in the pve1 row of [Local Machines](../README.md#local-machines).

With Tailscale on the host, the phone reaches the web UI at `https://TAILSCALE_IP:8006`
and host SSH at `ssh root@TAILSCALE_IP`. Guests are unaffected: each VM that should be on
the tailnet still runs its own Tailscale ([04-tailscale.md](04-tailscale.md)).

Running `tailscale up` inside an LXC container instead needs `/dev/net/tun` passed into
the container; it is not the recommended path for reaching the host.

### Optional: subnet router

Lets the phone reach the rest of the LAN (IPMI console, router, Plex on `debianbeelink`)
through the host, without installing Tailscale on each of them.

```
echo 'net.ipv4.ip_forward = 1' > /etc/sysctl.d/99-tailscale.conf
sysctl -p /etc/sysctl.d/99-tailscale.conf
tailscale up --advertise-routes=192.168.1.0/24
```

Then approve the route in the Tailscale admin console (Machines → pve1 → Edit route
settings). The iPhone app accepts subnet routes by default.

## Done checklist

- [ ] `ssh root@192.168.1.203` logs in with the key, no password prompt
- [ ] `ssh -o PubkeyAuthentication=no user@192.168.1.203` is refused (password login off)
- [ ] `ssh -o PubkeyAuthentication=no root@192.168.1.203` is refused (root-with-password off)
- [ ] `tailscale status` lists `pve1`
- [ ] `https://TAILSCALE_IP:8006` opens from the iPhone on cellular

## References

- https://pve.proxmox.com/pve-docs/pve-admin-guide.html (cluster manager: root SSH between nodes)
- https://pve.proxmox.com/wiki/FAQ (PVE to Debian release mapping)
- https://tailscale.com/kb/1133/proxmox
- https://tailscale.com/kb/1019/subnets
- https://tailscale.com/kb/1130/lxc-unprivileged
