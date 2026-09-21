# Proxmox host: SSH and Tailscale

Automated by `scripts/proxmox_setup.sh`. **Before you run it**, have your own public key
ready (`cat ~/.ssh/id_ed25519.pub` on your client). `ssh-harden` refuses to turn passwords off until
that key is in root's `authorized_keys` and sshd has logged a root login with it. Proxmox's
own node keys in that file do not count. Then, as root on the host:

```
curl -fsSL https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/proxmox_setup.sh | bash
```

It offers `ssh-keys` (paste your key), `ssh-harden`, `tailscale` and `subnet-router`, with
the same `--status`, `--yes` and `--only a,b` flags as `setup-machine`. After `ssh-keys`, log
in with the key from a new terminal when `ssh-harden` asks. Rerun the line to update. It
refuses to run anywhere but a Proxmox VE host.

`subnet-router` defaults to no at the prompt, but `--yes` runs every step that is not done,
including it. Unattended, pass the key and leave the subnet router out:

```
curl -fsSL https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/proxmox_setup.sh |
  PVE_OPERATOR_KEY="ssh-ed25519 AAAA... you@client" bash -s -- --yes --only ssh-keys
# log in once with that key from the client, then:
curl -fsSL https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/proxmox_setup.sh |
  bash -s -- --yes --only ssh-harden,tailscale
```

Host-side, on `pve1` itself (192.168.1.203, web UI :8006). The numbered steps cover a
Debian guest VM or workstation; this page covers the Proxmox VE host under them.

## Why the host is different

Proxmox VE is Debian underneath, but it is not a normal Debian box:

- The web UI's shell, migration and clustering log in as **root over SSH** between nodes.
- So do **not** run `setup-machine` here (use `proxmox_setup.sh` above), and do **not** set `PermitRootLogin no`.
- The right hardening is root by key only: `PermitRootLogin prohibit-password` plus
  `PasswordAuthentication no`.
- `/root/.ssh/authorized_keys` is a symlink into `/etc/pve/priv` and already holds the
  node's own key, so a non-empty file does not mean *your* key is there. Append to it; never
  replace the file or the symlink.

Check which Debian the host is on (PVE 8 is Debian 12, PVE 9 is Debian 13):

```
pveversion
```

## SSH

sshd already runs on the host, and root can log in with the password set during install.

From your client (make a key first with `ssh-keygen -t ed25519 -a 100` if you have none):

```
ssh-copy-id root@192.168.1.203
ssh -S none -o IdentitiesOnly=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no \
  -i ~/.ssh/id_ed25519 root@192.168.1.203 true    # must succeed: a fresh login with the key
```

Only once key login works, and with that session still open, on the host:

```
cat > /etc/ssh/sshd_config.d/99-local.conf <<'CONF'
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
CONF
sshd -t                          # no output = valid
sshd -T -C user=root,host=localhost,addr=127.0.0.1 |
  grep -E '^(permitrootlogin|pubkeyauthentication|passwordauthentication|kbdinteractiveauthentication) '
systemctl reload ssh
```

Reload only if `sshd -T` shows `prohibit-password` (or `without-password`), `yes`, `no`, `no`.
sshd takes the first value it sees, so an earlier file in `sshd_config.d/` or a line above
the `Include` in `sshd_config` can override `99-local.conf`. Find it with
`grep -rniE 'PermitRootLogin|PubkeyAuth|PasswordAuth|KbdInteractive' /etc/ssh/`.

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
tailscale debug prefs | grep -A3 AdvertiseRoutes   # routes already advertised, keep them
echo 'net.ipv4.ip_forward = 1' >> /etc/sysctl.d/99-tailscale.conf
sysctl -w net.ipv4.ip_forward=1
tailscale set --advertise-routes=192.168.1.0/24    # plus any existing routes, comma separated
```

`--advertise-routes` replaces the list, so name every route you want kept. The LAN route
is IPv4 only, so leave IPv6 forwarding alone: turning it on makes the host stop accepting
router advertisements. An old client without `tailscale set` needs upgrading first (rerun
the install line).

Then approve the route in the Tailscale admin console (Machines → pve1 → Edit route
settings). The iPhone app accepts subnet routes by default.

## Done checklist

- [ ] Fresh key login works:
  `ssh -S none -o IdentitiesOnly=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -i ~/.ssh/id_ed25519 root@192.168.1.203 true`
- [ ] Root password and keyboard-interactive login are refused by the server:
  `ssh -S none -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive root@192.168.1.203`
  ends in `Permission denied (publickey).`, with no password prompt. A connection or
  host-key error instead proves nothing.
- [ ] `tailscale status` lists `pve1`
- [ ] From the iPhone on cellular: `https://TAILSCALE_IP:8006` opens, and a new SSH session
  to `root@TAILSCALE_IP` logs in with the key
- [ ] Subnet router only: once the route is approved, `https://192.168.1.118` (IPMI) opens
  from the iPhone on cellular

## References

- https://pve.proxmox.com/pve-docs/pve-admin-guide.html (cluster manager: root SSH between nodes)
- https://pve.proxmox.com/wiki/FAQ (PVE to Debian release mapping)
- https://tailscale.com/kb/1133/proxmox
- https://tailscale.com/kb/1019/subnets
- https://tailscale.com/kb/1130/lxc-unprivileged
