# Linux Setup
```
cat /etc/os-release
```
PRETTY_NAME="Debian GNU/Linux 13 (trixie)"
NAME="Debian GNU/Linux"
VERSION_ID="13"
VERSION="13 (trixie)"
VERSION_CODENAME=trixie
DEBIAN_VERSION_FULL=13.2
ID=debian
HOME_URL="https://www.debian.org/"
SUPPORT_URL="https://www.debian.org/support"
BUG_REPORT_URL="https://bugs.debian.org/"

# Openssh Setup

// Check if ssh services is active
sudo systemctl status sshd
// start the service
sudo systemctl start sshd
// enable the service at startup
sudo systemctl enable sshd

## Connect from a client
use ~/.ssh/id_rsa
```
ssh jasonz001@192.168.1.126
```

# Disable sleep

```
// mask sleep and suspend
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
// check sleep and suspend
systemctl status sleep.target suspend.target

// for gnome
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
gsettings set org.gnome.desktop.session idle-delay 0
// check the values
gsettings get org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type
gsettings get org.gnome.desktop.session idle-delay

```

# Local Machines

Home LAN is `192.168.1.0/24`. Gateway (AT&T router) is `192.168.1.254`, DHCP pool
is `192.168.1.64`-`192.168.1.220`. To pin a "static" address, first set the device
to DHCP so the router discovers it, then assign the allocated address (see TRUNAS.md).

**Unreserved leases.** `debianbeelink` (.126), `pve1` (.203) and `debian-xfce` (.133)
all sit inside the DHCP pool with no reservation, so their addresses are leases the
router could in principle hand out differently. In practice the router keeps giving the
same address to the same MAC, and it has held for all three so far. It is still worth
reserving them in the router UI, because these addresses are written into this file and
into the Plex URLs in PLEX.md, and a lease that moves makes those docs quietly wrong.
`192.168.1.118` (IPMI) and `192.168.1.254` (router) are outside the pool and not at risk.

If you would rather not reserve them, prefer the names over the numbers where you can -
`debian-xfce.local` resolves over mDNS and survives a lease change.

Current:

| Host | Address | Access | Role |
|------|---------|--------|------|
| `debianbeelink` | 192.168.1.126 | `ssh jasonz001@…` port 22 | Plex (:32400), Docker, GitHub runners |
| `pve1.home.arpa` | 192.168.1.203 | https :8006, `ssh` port 22 | Proxmox VE host |
| `debian-xfce` | 192.168.1.133 (Tailscale 100.74.143.43) | `ssh zhangxienjie@…` port 22 | Debian desktop node, also on the tailnet |
| IPMI (Supermicro) | 192.168.1.118 | web UI, https | Out-of-band console for the Proxmox chassis |
| Router (AT&T) | 192.168.1.254 | web UI | Gateway, DHCP, address reservations |

Retired, kept for the historical record:

| Host | Address | Role |
|------|---------|------|
| `freenas` (TrueNAS) | 192.168.1.201 | Media/backup storage - hardware now runs Proxmox |
| `pms` (Plex jail) | 192.168.1.202 | Plex on the TrueNAS box |

## debianbeelink

Beelink AZW EQ, Debian 13 (trixie), x86-64. Primary home server.

- **LAN:** `192.168.1.126` on `enp2s0` (DHCP lease, not reserved)
- **SSH:** `ssh jasonz001@192.168.1.126` (port 22)
- **Plex web UI:** http://192.168.1.126:32400/web/index.html#!/
- **Storage:** 452G NVMe root, plus two 20T media drives
  - `/media/jasonz001/Drive1` (`/dev/sda1`) - Plex1: Disney, Movies
  - `/media/jasonz001/Drive2` (`/dev/sdb1`) - Plex2: Anime, Anime_Movies, TV_Shows, Music, Backups, Software
- **Services running:** `plexmediaserver` (32400), `docker`, `ssh`, and three GitHub
  Actions runners (`Nicu-Labs-trip-planner.debianbeelink`, `Nicu-Labs.debianbeelink-2`,
  `Nicu-Labs.debianbeelink-3`) - see GITHUB_RUNNER.md
- **Enabled but not currently running:** `mcbedrock` (Minecraft Bedrock, see MINECRAFT.md)
- **Ports open on the LAN:** 22 (ssh), 80, 32400 (Plex), 5434 + 33314 (Postgres
  containers), 9004/9005 (MinIO container)

```
ssh jasonz001@192.168.1.126
```

## IPMI (Supermicro out-of-band)

- **LAN:** `192.168.1.118`, MAC `0c:c4:7a:cf:37:12`
- Web UI over https. Independent of the host OS - use it for console access and power
  control on the Supermicro chassis (now the Proxmox host) when it is unreachable.
- Default username is `ADMIN`. The password is either `ADMIN` (older boards) or the
  unique one printed on the motherboard/chassis sticker ("BMC PWD" / "IPMI PWD").

### ipmitool

Run these on `pve1` (the Supermicro host). Talking to the BMC from the host OS goes
through the kernel driver and needs no BMC login. Running it on any other machine
won't work without `-I lanplus -H 192.168.1.118 -U ADMIN -P ...`.

Install:

```
apt update
apt install ipmitool
modprobe ipmi_devintf ipmi_si
```

If `apt update` fails with a 401 from `enterprise.proxmox.com`, disable the paid
repo and use the free one:

```
sed -i 's/^deb/#deb/' /etc/apt/sources.list.d/pve-enterprise.list
echo "deb http://download.proxmox.com/debian/pve $(. /etc/os-release; echo $VERSION_CODENAME) pve-no-subscription" > /etc/apt/sources.list.d/pve-no-subscription.list
apt update
```

(On newer Proxmox the file is `pve-enterprise.sources`; add `Enabled: no` to it instead.)

`Could not open device at /dev/ipmi0` means the `modprobe` line didn't run, or you're
not on the Supermicro host.

#### Reset a forgotten IPMI password

```
// find the ADMIN user's ID (usually 2)
ipmitool user list 1
ipmitool user set password 2 'NewPassw0rd'
ipmitool user enable 2
```

Last resort: Supermicro's `IPMICFG` tool, `ipmicfg -fd`, factory-resets the BMC. That
also wipes its network config, so it may come back on a new DHCP address instead of
`192.168.1.118`.

#### Fans spinning up and down

Quiet fans can idle below the BMC's lower RPM thresholds. The BMC then thinks a fan
failed, ramps every fan to full, they rise above the threshold, slow down, and the
cycle repeats.

Check:

```
// RPM and lnr/lcr/lnc thresholds per fan
ipmitool sensor | grep -i fan
// repeated "Lower Critical going low" = this problem
ipmitool sel list | tail -20
```

Fix by lowering each affected fan's thresholds (non-recoverable, critical,
non-critical) well below its idle RPM:

```
ipmitool sensor thresh FAN1 lower 100 200 300
ipmitool sensor thresh FAN2 lower 100 200 300
```

The BMC rounds to its own step size (often 100 or 140 RPM), so re-run `ipmitool sensor`
to see what was actually set. Thresholds go back to defaults after a BMC firmware
update or factory reset.

Fan mode (also under Configuration -> Fan Mode in the web UI):

```
// show current mode
ipmitool raw 0x30 0x45 0x00
// Standard
ipmitool raw 0x30 0x45 0x01 0x00
// Full: always 100%, loud but no cycling
ipmitool raw 0x30 0x45 0x01 0x01
```

If the fan thresholds look fine, check `ipmitool sensor` for a temperature near its
upper threshold instead - that's a real cooling problem (dust, failing fan).

## pve1.home.arpa - Proxmox VE

Proxmox hypervisor, on the Supermicro chassis (its NIC MAC `0c:c4:7a:cf:39:94` matches
the MAC TRUNAS.md lists for the old TrueNAS `igb0`).

- **LAN:** `192.168.1.203` (DHCP lease, not reserved), listening on both `:8006` and `:22`
- **Web UI:** https://192.168.1.203:8006 (self-signed cert, so expect a browser warning)
- **Hostname** `pve1.home.arpa` is not served by the router's DNS - use the IP, or add
  it to `/etc/hosts` on whichever machine you want to use the name from:

```
echo "192.168.1.203  pve1.home.arpa pve1" | sudo tee -a /etc/hosts
```

```
// web UI
https://192.168.1.203:8006
// shell
ssh root@192.168.1.203
```

### debian-xfce

Debian desktop node. Reachable two ways: directly on the LAN, or over the tailnet from
outside the house.

- **LAN:** `192.168.1.133`, MAC `bc:24:11:2e:0d:45` (DHCP lease, not reserved)
- **Tailscale IP:** `100.74.143.43`
- **Tailscale account:** `zhangxienjie@`
- **SSH:** port 22, open on the LAN - key auth only, password auth is refused
- **IPv6:** `2600:1700:243b:a00::13` (router-assigned)
- **Names:** `debian-xfce.local` (mDNS) and `debian-xfce.attlocal.net` (router DNS) both
  resolve to `192.168.1.133`. `debian-xfce.home.arpa` does not resolve.

```
// from inside the house
ssh zhangxienjie@192.168.1.133
// from outside, over the tailnet
ssh zhangxienjie@100.74.143.43
```

The `bc:24:11` MAC prefix is Proxmox's virtual NIC OUI, so this is most likely a VM on
`pve1` rather than separate hardware - not yet confirmed against `qm list` on the host.

`debianbeelink` has no key on this box, so SSH from there gets `Permission denied
(publickey)` until one is installed. It has also not joined the tailnet, so it must use
the LAN address. To join the tailnet from a Debian/Ubuntu machine:

```
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale status
```

---

# Retired Machines

Kept for the historical record only. None of these are reachable.

## freenas / TrueNAS - OBSOLETE (historical record)

**This server is retired.** It is kept here only as a historical record. The Supermicro
chassis it ran on now runs Proxmox as `pve1` at `192.168.1.203` - the NIC MAC
`0c:c4:7a:cf:39:94` is the same on both, which is why `192.168.1.201` no longer answers.

Anything below is how it *used* to be reached, not how things work now. The sync and
backup commands in PLEX.md and TRUNAS.md still point at `192.168.1.201` and will not
connect as written; both files are marked obsolete and the commands are kept only as
templates for whatever replaces the share.

- **LAN (former):** `192.168.1.201`, MAC `0c:c4:7a:cf:39:94` (igb0)
- **SSH (former):** port `2222`, user `remote`, key `~/.ssh/nas_sync`
- **Shares (former):** `/mnt/Media/family`, `/mnt/Media/windows`
- The `mynas` alias in `jasonz001`'s `~/.ssh/config` on `debianbeelink` still points at
  `192.168.1.201:2222` and is therefore stale

## pms (Plex jail on the old NAS) - OBSOLETE

Part of the retired TrueNAS box above, kept for the historical record.

- **LAN (former):** `192.168.1.202`, MAC `0e:c4:7a:57:7e:c1` (vnet0)
- **Web UI (former):** http://192.168.1.202:32400/web
- The Plex instance actually in use is the one on `debianbeelink`.
