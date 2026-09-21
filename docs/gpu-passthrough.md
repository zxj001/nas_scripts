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
