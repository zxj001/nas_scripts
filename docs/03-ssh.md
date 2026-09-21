# 3. SSH

Automated by `setup-machine --only ssh,ssh-keys,ssh-harden`.

SSH listens on the default port 22.

On the Proxmox host itself, see [pve-host.md](pve-host.md): root must keep key login there.

## Server

The installer's "SSH server" option usually handles this already. If not:

```
sudo apt install -y openssh-server
sudo systemctl enable --now ssh
systemctl status ssh --no-pager
hostname -I                      # the machine's LAN IP
```

From another machine: `ssh YOURUSERNAME@LAN_IP`.

## Keys

On the client, if there's no key yet:

```
ssh-keygen -t ed25519 -a 100
ssh-copy-id YOURUSERNAME@LAN_IP
```

No `ssh-copy-id`? Paste the output of `cat ~/.ssh/id_ed25519.pub` into `~/.ssh/authorized_keys`
on the server, then:

```
chmod 700 ~/.ssh
chmod 600 ~/.ssh/authorized_keys
```

Open a fresh session to confirm key login works before the next step.

## Hardening

Only once key login works. Keep your current session open.

```
sudo nano /etc/ssh/sshd_config.d/99-local.conf
```

```
PermitRootLogin no
PubkeyAuthentication yes
PasswordAuthentication no
```

```
sudo sshd -t                     # no output = valid
sudo systemctl reload ssh
```

Confirm a new key-based session works before closing the old one.

Don't port-forward SSH on the router. For access from outside the house, use
[Tailscale](04-tailscale.md).

Next: [04-tailscale.md](04-tailscale.md)
