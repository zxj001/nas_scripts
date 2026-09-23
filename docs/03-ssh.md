# 3. SSH

Automated by `setup-machine --only ssh,ssh-keys,ssh-harden --with-deps`.

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

On reruns, setup checks the effective global policy using `sudo -n /usr/sbin/sshd -T`,
so comments, directive order, and settings supplied by other drop-ins do not cause
a file-content conflict. Without cached sudo credentials, `--status` may report
`pending`; it never prompts for privilege.

If `99-local.conf` already exists but its effective policy is not hardened, setup
leaves it unchanged, explicitly reports `SSH hardening skipped`, and continues
to subsequent steps. That message does not mean SSH is hardened. Review the
configuration manually, then rerun `setup-machine --only ssh-harden`. If the file
is absent, setup creates its drop-in and validates both syntax and effective
global policy before reloading; validation/reload failure removes only the new file.

The global policy check does not validate every user/address-specific `Match`
block or prove that a new key-based connection works.

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
