#!/usr/bin/env bash
# Run on the Proxmox host before passing a GPU through to a VM.
# See docs/gpu-passthrough.md.
set -euo pipefail

echo "== IOMMU =="
dmesg | grep -e DMAR -e IOMMU || echo "nothing - enable VT-d/IOMMU in the BIOS"

echo
echo "== NVIDIA PCI IDs (pass every function through) =="
lspci -nn | grep -i nvidia || echo "no NVIDIA device on this host"
