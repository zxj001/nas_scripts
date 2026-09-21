"""Capability-specific power recovery; desktops remain honestly manual."""

import re

from setup_core.capabilities import available
from setup_tasks.common import apt

GUIDANCE = (
    "Desktop BIOS/UEFI: Restore on AC Power Loss / AC Recovery -> Power On. "
    "Mac: System Settings -> Energy. See docs/power-restore.md; firmware changes are unverified."
)


def backend(ctx):
    if ctx.profile == "macos":
        return "mac" if "autorestart" in ctx.run("pmset", "-g", "cap").stdout.split() else "manual"
    if (
        ctx.have("systemd-detect-virt")
        and ctx.run("systemd-detect-virt", "--quiet", allowed=(0, 1)).returncode == 0
    ):
        return "virtual"
    if any(
        ctx.path(path).exists()
        for path in ("/dev/ipmi0", "/dev/ipmi/0", "/dev/ipmidev/0", "/sys/class/ipmi/ipmi0")
    ):
        return "ipmi"
    return "manual"


def enabled(ctx, kind):
    if kind == "mac":
        values = re.findall(
            r"^\s*autorestart\s+(\d+)\s*$", ctx.run("pmset", "-g", "custom").stdout, re.MULTILINE
        )
        return bool(values) and all(value == "1" for value in values)
    if not ctx.have("ipmitool") or not available(ctx, "admin"):
        return False
    status = ctx.run("ipmitool", "-I", "open", "chassis", "status", privileged=True).stdout
    return bool(re.search(r"^\s*Power Restore Policy\s*:\s*always-on\s*$", status, re.MULTILINE))


def probe(ctx, task):
    kind = backend(ctx)
    if kind == "virtual":
        return ctx.result(
            "not-applicable", "set host recovery and VM Start at boot in the hypervisor"
        )
    if kind == "manual":
        return ctx.result("manual", "firmware power recovery has not been verified", GUIDANCE)
    return ctx.result("satisfied" if enabled(ctx, kind) else "pending")


def apply(ctx, task):
    kind = backend(ctx)
    if kind == "mac":
        ctx.run("pmset", "-a", "autorestart", "1", privileged=True)
    elif kind == "ipmi":
        if not ctx.have("ipmitool"):
            if not available(ctx, "packages"):
                return ctx.result("blocked", "repair dpkg before installing ipmitool")
            apt(ctx, "ipmitool")
        ctx.run("modprobe", "ipmi_devintf", privileged=True)
        ctx.run("ipmitool", "-I", "open", "chassis", "policy", "always-on", privileged=True)
    else:
        return probe(ctx, task)
    return ctx.result("changed", "power recovery enabled; no reboot or power-off was issued")
