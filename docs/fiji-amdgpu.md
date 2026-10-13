# R9 Nano (Fiji) passthrough: patched amdgpu (VM)

Tracks [issue #64](https://github.com/zxj001/nas_scripts/issues/64). The R9 Nano passes through to
the Debian 13 VM (`myllama`, VM 100) and the stock amdgpu driver binds to it, but it oopses
during SMU firmware load. As a result there is no render node, and Vulkan only sees `llvmpipe`.

## Cause

In `amdgpu_device_need_post()` (`drivers/gpu/drm/amd/amdgpu/amdgpu_device.c`), the Fiji branch
runs only in passthrough. It loads `amdgpu/fiji_smc.bin` into the shared `adev->pm.fw` to read
its version, then calls `release_firmware()` without clearing the pointer. Later,
`amdgpu_cgs_get_firmware_info()` loads the SMC firmware only when `!adev->pm.fw`. It therefore
reuses the freed firmware, and `amdgpu_ucode_print_smc_hdr()` faults (`CR2=0x9`). The code is
the same in Debian 6.12.107, 6.12.111 and mainline, so switching kernels does not help.

[`patches/amdgpu-fiji-passthrough-pm-fw-uaf.patch`](../patches/amdgpu-fiji-passthrough-pm-fw-uaf.patch)
does the version check through a local `struct firmware *`, so `adev->pm.fw` is never touched.

## Build and install

```
scripts/fiji_amdgpu.sh build          # as your user, ~20 min on 4 vCPUs
sudo scripts/fiji_amdgpu.sh install   # sign, install to updates/, initramfs
```

`build` fetches the `linux-source` and `linux-headers` packages for the running kernel's exact
version with `apt-get download`, so no packages are installed. It patches `drivers/gpu/drm/amd`
and compiles only `amdgpu.ko`, all under `~/fiji-amdgpu`. It also unpacks `pahole` there when
it is missing, because the module's BTF needs it.

The VM boots with Secure Boot on, so the module must be signed by an enrolled key. `install`
strips the 600 MB of debug info (as Debian does for its own modules) and then signs it with
`/var/lib/dkms/mok.key`, creating the key if it is missing, which lets a later dkms package
reuse the same key. The first time, `install` runs `mokutil --import` and asks for
a one-time password. Reboot, open the VM console in Proxmox (noVNC), and in the blue MOK Manager
choose **Enroll MOK → Continue → Yes**, then enter the password.

After reboot, check the result:

```
scripts/fiji_amdgpu.sh status   # expect updates/amdgpu.ko, render node renderD128
vulkaninfo --summary            # expect AMD RADV FIJI next to llvmpipe
```

Rollback: run `sudo scripts/fiji_amdgpu.sh uninstall` and reboot. The stock module is never
modified, and the other installed kernel (`6.12.107+deb13-amd64`) stays in GRUB. The patched
module only exists for the kernel it was built for, so after a kernel upgrade, run `build` and
`install` again (until the dkms package below exists).

To keep the Oops for the upstream report, save it before the patched boot replaces it:

```
sudo journalctl -k -b -1 --no-pager > ~/fiji-amdgpu/oops-6.12.111.log
```

Use the `-b` index whose boot shows the Oops (`journalctl --list-boots`).

## Progress

- [x] Root cause confirmed in the 6.12.111 source and mainline
- [x] Patch against Debian `6.12.111-1`; `build` fetches the source and builds out of tree without root
- [x] `amdgpu.ko` built: same vermagic and deps as stock, and the disassembly shows `request_firmware` writing to a stack slot instead of `adev->pm.fw`
- [ ] Full Oops log saved
- [ ] MOK key enrolled, patched module installed, VM rebooted
- [ ] `fiji_start_smu` completes, `/dev/dri/renderD128` exists, `vulkaninfo` shows RADV FIJI
- [ ] llama.cpp Vulkan vs CPU benchmark
- [ ] dkms package and `setup_tasks/fiji.py`, so the fix survives kernel upgrades
- [ ] Patch sent upstream (amd-gfx), with `Fixes:` and the Oops log
