# Proxmox host: repositories, SSH, Tailscale and the ShellFish widget

Automated by `scripts/proxmox_setup.sh`. **Before you run it**, have your own public key
ready (`cat ~/.ssh/id_ed25519.pub` on your client). `ssh-harden` refuses to turn passwords off until
that key is in root's `authorized_keys` and sshd has logged a root login with it. Proxmox's
own node keys in that file do not count. Then, as root on the host:

```
curl -fsSL https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/proxmox_setup.sh | bash
```

It offers `repos` (see [Package repositories](#package-repositories)), `ssh-keys` (paste
your key), `ssh-harden`, `tailscale`, `subnet-router` and `shellfish` (see
[ShellFish widget](#shellfish-widget)), with the same `--status`, `--yes`
and `--only a,b` flags as `setup-machine`. After `ssh-keys`, log
in with the key from a new terminal when `ssh-harden` asks. Rerun the line to update. It
refuses to run anywhere but a Proxmox VE host.

`subnet-router` defaults to no at the prompt, but `--yes` runs every step that is not done,
including it. Unattended, pass the key and leave the subnet router out:

```
curl -fsSL https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/proxmox_setup.sh |
  PVE_OPERATOR_KEY="ssh-ed25519 AAAA... you@client" bash -s -- --yes --only repos,ssh-keys
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

## Package repositories

A fresh install enables the enterprise repositories (`pve-enterprise`, and the Ceph
`enterprise` one), which need a subscription. Without one every `apt update` fails:

```
Err:9 https://enterprise.proxmox.com/debian/pve trixie InRelease   401  Unauthorized
E: The repository 'https://enterprise.proxmox.com/debian/pve trixie InRelease' is not signed.
```

The `repos` step, first in the list because everything that installs packages needs apt,
turns those off and turns on `pve-no-subscription`, for the suite in `/etc/os-release`:

- sets `Enabled: false` in each enterprise stanza of `pve-enterprise.sources` / `ceph.sources`
  (PVE 9), or comments the enterprise line in `pve-enterprise.list` / `ceph.list` (PVE 8),
  keeping the rest of the file
- writes `/etc/apt/sources.list.d/pve-no-subscription.sources`, plus a Ceph
  `no-subscription` stanza for the same Ceph release when a Ceph enterprise repo was there,
  unless an enabled binary (`deb`) source already provides it
- runs `apt-get update`

It never touches `debian.sources` or `sources.list`. Proxmox does not recommend
`pve-no-subscription` for production; with a subscription, re-enable the enterprise repos.
The web UI's "no valid subscription" dialog is separate and stays.

The step prefers the installed `/usr/share/keyrings/proxmox-archive-keyring.gpg`.
On Bookworm only, it can fall back to the installed
`/etc/apt/trusted.gpg.d/proxmox-release-bookworm.gpg`. The selected keyring must be
nonempty and readable by `_apt`; otherwise the step refuses before changing sources.
It downloads no keys and repairs the `Signed-By` fields of its managed stanzas for
the running suite in place. Other-suite entries stay unchanged; a mixed-suite stanza
that needs keyring repair causes an unchanged refusal.
If an existing enabled `deb` or `deb-src` entry for the same public URI and suite
has missing or conflicting `Signed-By` settings, the step refuses unchanged. Resolve
those administrator settings explicitly before rerunning; the step does not rewrite them.

For a manual equivalent, first select and check the installed keyring in a root Bash
shell, before editing any sources:

```bash
suite=$( . /etc/os-release; printf '%s' "$VERSION_CODENAME" )
key=/usr/share/keyrings/proxmox-archive-keyring.gpg
if ! { [ -f "$key" ] && [ -s "$key" ] && runuser -u _apt -- test -r "$key"; }; then
    [ "$suite" = bookworm ] || { echo 'No usable archive keyring'; exit 1; }
    key=/etc/apt/trusted.gpg.d/proxmox-release-bookworm.gpg
fi
[ -f "$key" ] && [ -s "$key" ] && runuser -u _apt -- test -r "$key" || exit 1
```

Inspect the existing sources first. Leave `debian.sources` and `sources.list` unchanged;
if either contains an enabled enterprise entry, the automated step refuses this layout.
On PVE 9, set `Enabled: false` in each enterprise stanza in `pve-enterprise.sources`
and `ceph.sources`, replacing an existing `Enabled` field if present. On PVE 8, comment
out the enterprise `deb` and `deb-src` lines in `pve-enterprise.list` and `ceph.list`.
Preserve unrelated entries.

If no enabled binary PVE no-subscription source exists, add this stanza to
`/etc/apt/sources.list.d/pve-no-subscription.sources`, using the selected values
(the command below prints the stanza for copying):

```bash
cat <<EOF
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: $suite
Components: pve-no-subscription
Signed-By: $key
EOF
```

Separate stanzas with a blank line. If a Ceph enterprise source was present, add a
matching binary stanza with `URIs: http://download.proxmox.com/debian/ceph-<release>`,
`Components: no-subscription`, and the same suite and selected keyring, unless that
binary source already exists. Prefer the active enterprise release over disabled
historical entries. If a previously generated stanza names a missing keyring, update
its `Signed-By` field rather than adding a duplicate. Finally run `apt-get update`.

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

## Temperatures

Run these commands as root on the Proxmox host, over SSH or in the node's **Shell**:

```sh
apt update
apt install lm-sensors
sensors
watch -n 2 sensors    # refresh every two seconds; Ctrl-C to stop
```

`sensors` displays readings exposed by loaded drivers; installing it does not guarantee
that every physical sensor is available ([manual](https://manpages.debian.org/trixie/lm-sensors/sensors.1.en.html)).
This is a manual setup step; `proxmox_setup.sh` does not install it.

If the Intel CPU's package/core readings are missing, try its
[`coretemp` driver](https://docs.kernel.org/hwmon/coretemp.html):

```sh
modprobe coretemp
sensors
```

Only if loading the driver produces the missing readings, persist it for reboot:

```sh
printf 'coretemp\n' > /etc/modules-load.d/coretemp.conf
```

This driver is for supported Intel CPUs. A module error or missing readings needs
hardware-specific investigation; running these commands inside a VM will not expose
the host's CPU sensors.

For the Supermicro BMC's CPU, system, PCH and DIMM readings, or a no-install kernel
readout, see [Temperatures](../README.md#temperatures). The BMC path also reports
alarm thresholds. Check readings again after reboot if you added a module file.

## ShellFish widget

The iPhone widget from [08-shellfish-widgets.md](08-shellfish-widgets.md) also works on
the host. It shows `pve1` with a real CPU temperature, which a VM can't read.

1. In ShellFish, connect to the host **as root**, then choose **Install Shell Integration**
   in the server's settings. That writes `/root/.shellfishrc`.
2. Run the step (it shows `manual` until step 1 is done):

   ```
   curl -fsSL https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/proxmox_setup.sh |
     bash -s -- --only shellfish
   ```

   It installs `openssl`, `xxd`, `curl` and `cron` if any are missing, downloads the widget
   script to `/usr/local/bin/shellfish_widget.sh`, adds a 15-minute entry to root's
   crontab (keeping the existing entries) and sends the first update. Run `repos` first if
   `apt-get update` fails on the enterprise repository.

Temp needs a loaded CPU sensor driver; see [Temperatures](#temperatures) if it is missing
from the widget.

Nothing is cloned onto the host, so an existing `/usr/local/bin/shellfish_widget.sh` is
kept. To update it:

```
curl -fsSL https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/shellfish_widget.sh \
  -o /usr/local/bin/shellfish_widget.sh && chmod 755 /usr/local/bin/shellfish_widget.sh
```

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
- [ ] ShellFish widget only: `shellfish_widget.sh --print` shows `pve1` and a Temp value
- [ ] Subnet router only: once the route is approved, `https://192.168.1.118` (IPMI) opens
  from the iPhone on cellular

## References

- https://pve.proxmox.com/wiki/Package_Repositories
- https://pve.proxmox.com/pve-docs/pve-admin-guide.html (cluster manager: root SSH between nodes)
- https://pve.proxmox.com/wiki/FAQ (PVE to Debian release mapping)
- https://tailscale.com/kb/1133/proxmox
- https://tailscale.com/kb/1019/subnets
- https://tailscale.com/kb/1130/lxc-unprivileged
