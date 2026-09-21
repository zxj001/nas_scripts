# GPU passthrough (optional, VM)

Passes a physical GPU (the GeForce GT 1030) through to a Proxmox VM. Do this only after the
VM works on the virtual display, and take the `clean-xfce-base` snapshot first
([02-post-install.md](02-post-install.md#snapshot-vm)).

The Supermicro X10SLL-F has onboard ASPEED graphics, so Proxmox keeps using that display
while the GT 1030 is dedicated to the VM.

1. In the motherboard BIOS, enable Intel VT-d / IOMMU.
2. On the Proxmox host, confirm IOMMU is on:
   ```
   dmesg | grep -e DMAR -e IOMMU
   ```
3. Find the card:
   ```
   lspci -nn | grep -i nvidia
   ```
   It usually shows two functions, VGA and HDMI/DP audio. Pass both through.
4. Keep the VM on q35 + OVMF, then add the PCI device under VM → Hardware.

Full procedure: the PCI(e) passthrough chapter of the
[Proxmox admin guide](https://pve.proxmox.com/pve-docs/pve-admin-guide.html).

After passthrough, run `setup-machine --only gpu` in the VM to install the NVIDIA driver.
If the VM boots with Secure Boot on, the dkms-built nvidia module will not load: either turn Secure
Boot off (at boot press ESC into the OVMF menu, then Device Manager → Secure Boot Configuration →
uncheck "Attempt Secure Boot"; or run `sudo mokutil --disable-validation`, reboot and confirm the
change in the MOK manager), or keep it on and enroll the dkms key with
`mokutil --import /var/lib/dkms/mok.pub`, then reboot and confirm the enrolment in the MOK manager.
