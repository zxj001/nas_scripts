# 1. Install Debian 13 + XFCE

Manual; only the sudo fix is automated: `setup-machine --only sudo`.

Target stack: Proxmox VE → Debian 13 → XFCE → X11. On bare metal, skip the Proxmox steps
and boot the ISO from a USB stick.

## Get the ISO

Download the current amd64 netinst ISO (e.g. `debian-13.7.0-amd64-netinst.iso`) from
https://www.debian.org/download.

**(VM)** Upload it in the Proxmox web UI (`https://PROXMOX-IP:8006`):
Datacenter → node → `local` → ISO Images → Upload. ISOs go on `local`; `local-lvm` is for
VM disks.

## Create the VM (VM)

Create VM with:

| Setting | Value |
|---------|-------|
| Name | e.g. `debian-xfce` |
| Machine | `q35` |
| BIOS | OVMF (UEFI), add an EFI disk |
| Display | Default / Standard VGA |
| SCSI controller | VirtIO SCSI single |
| Disk | SCSI bus, 40-50 GB+, Discard on, IO thread on |
| CPU | Type `host`, 1 socket, 4 cores |
| Memory | 24576 MB (24 GB) |
| Network | VirtIO on `vmbr0` |

Keep q35 + OVMF: GPU passthrough needs them later. Don't pass any GPU through yet; install on
the Proxmox virtual display first.

## Run the installer

Start the VM, open Console, choose **Graphical install**.

- Language English, location United States, keyboard American English
- Hostname: the machine name. Domain: leave blank (if a home DNS domain ever exists, use
  `home.arpa`)
- Root password: **leave it blank** and the installer puts your user in the `sudo` group.
  Set one and you'll need the sudo fix below.
- Partitioning: Guided - use entire disk → All files in one partition
- Mirror: a nearby US mirror, proxy blank

Software selection:

```
[X] Debian desktop environment
[ ] GNOME
[X] Xfce
[X] SSH server
[X] standard system utilities
```

Make sure GNOME is unchecked. Everything else stays unchecked.

After reboot, if it boots back into the installer: VM → Hardware → CD/DVD Drive → Edit →
Do not use any media.

## Check the install

```
cat /etc/os-release          # PRETTY_NAME="Debian GNU/Linux 13 (trixie)"
echo $XDG_CURRENT_DESKTOP    # XFCE
```

## Fix sudo (only if you set a root password)

```
su -
apt update && apt install sudo
usermod -aG sudo YOURUSERNAME
```

Log out and back in, then `sudo whoami` should print `root`. Use `sudo -i` for a root shell.
Never set your user's UID to 0.

Next: [02-post-install.md](02-post-install.md)

## References

- Debian install manual: https://www.debian.org/releases/stable/installmanual
- Proxmox VE docs: https://pve.proxmox.com/pve-docs/
