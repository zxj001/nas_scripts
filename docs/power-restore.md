# Start automatically after a power outage

Run `setup-machine --only power-restore`. This step concerns startup when mains
power returns after an outage. It does not shut down, reboot, power-cycle the
machine, configure a scheduled start, or send Wake-on-LAN packets.

The interactive prompt defaults to **no** because this changes power behavior.
`--yes` selects it along with other pending steps. `--status` only reads settings
and never prompts for sudo. A status of `manual` means the script cannot configure
or verify the firmware setting, even if you have already enabled it yourself.

## Ordinary desktops and mini PCs

IPMI is not required. Enter the machine's BIOS/UEFI setup and find its power
management page. Common labels include **Restore on AC Power Loss**, **AC Recovery**,
**After Power Failure**, and **State After G3**. Select **Power On / Always On** for
unattended startup. **Last State** usually restarts only a machine that was running
when power was lost; **Power Off** leaves it off.

For example, Dell documents AC Recovery under
[OptiPlex power management](https://www.dell.com/support/manuals/en-uk/optiplex-3070-desktop/opti3070_mt_setup_specs/power-management?guid=guid-07fda33c-09d9-4a21-9de7-aac21764bb70&lang=en-us).
Other vendors use different menus; consult the exact model's firmware manual.
Some machines do not expose this feature. Debian has no universal command for
changing all vendors' BIOS settings, so setup prints these instructions and
continues rather than writing arbitrary firmware registers or assuming IPMI exists.

If your vendor offers a supported Linux BIOS configuration utility, use that
model's documented AC recovery setting. Setup does not install vendor tools or
bypass firmware passwords.

## Servers with local IPMI

When a local IPMI device or kernel interface is present, setup installs `ipmitool`
if necessary, loads `ipmi_devintf`, and runs:

```
sudo ipmitool -I open chassis policy always-on
sudo ipmitool -I open chassis status
```

It verifies `Power Restore Policy : always-on` before reporting success. Reruns
skip an already enabled policy. This sets the restore policy; it does not issue
`chassis power on`, reset, or power-off commands. It uses the local interface and
requires no remote BMC credentials.

If Linux has not exposed the IPMI interface, setup falls back to manual guidance.
A server administrator can enable the interface or set power recovery through the
BMC web UI. `Last State` / `previous` differs from `always-on`; the
[ipmitool manual](https://manpages.debian.org/trixie/ipmitool/ipmitool.1.en.html)
documents these policies.

## Mac desktops

If `pmset -g cap` reports support for `autorestart`, setup changes only that setting:

```
sudo pmset -a autorestart 1
pmset -g custom
```

It verifies the reported values afterward. Other sleep/wake and power settings
are preserved. Unsupported Macs receive manual guidance instead: System Settings
→ Energy → **Start up automatically after a power failure**, when available.
Apple documents this in [Energy settings](https://support.apple.com/en-mide/guide/mac-help/change-energy-settings-mchlp1168/26/mac/26).
Consult local `man pmset` for the capabilities of the installed macOS version.

## Virtual machines and UPS-backed systems

A VM cannot set its physical host's AC recovery policy. The step is `n/a` inside a
recognized VM/container. Configure the host's firmware/BMC recovery and the VM's
**Start at boot** setting in the hypervisor separately.

With a UPS, an orderly OS shutdown may leave mains power continuously present at
the computer. AC recovery may not trigger until its power supply actually loses
and regains power. Coordinate the UPS's supported shutdown/output-restore sequence
with the host's recovery policy; simply adding a boot service cannot start an
unpowered machine. Verify your UPS and firmware behavior during a planned maintenance
window after saving work and shutting down safely.

## Other ways to start an off machine

- **Wake-on-LAN:** a compatible NIC/firmware and standby power can allow a remote
  sender to request startup. It also needs persistent OS network configuration.
  NetworkManager exposes `802-3-ethernet.wake-on-lan`; see its
  [connection settings reference](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nm-settings-nmcli.html).
  Support from full shutdown varies by machine. It needs a separate powered sender
  and does not supply electricity or implement unconditional AC recovery.
- **Scheduled firmware startup:** some desktops have **Auto On Time** or an RTC
  alarm in BIOS/UEFI. Configure it there for the model's supported schedule; it is
  distinct from AC Recovery (both appear in the Dell reference above).
- **Remote BMC control:** a server's out-of-band controller can power it on while
  the OS is off, provided the controller has power and network access.

These alternatives are guidance, not automatically configured by `power-restore`.
