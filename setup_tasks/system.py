"""Debian package/service tasks; commands fail inside their own worker."""

from setup_tasks.common import apt

TARGETS = ("sleep", "suspend", "hibernate", "hybrid-sleep")


def probe(ctx, task):
    if task.id == "upgrade":
        pending = any(
            line.startswith("Inst ")
            for line in ctx.run("apt-get", "-s", "full-upgrade").stdout.splitlines()
        )
        return ctx.result("pending" if pending else "satisfied", "uses locally cached apt lists")
    if task.id == "guest-agent":
        virtual = ctx.run("systemd-detect-virt", allowed=(0, 1)).stdout.strip()
        if virtual != "kvm":
            return ctx.result("not-applicable", "not a KVM guest")
        okay = ctx.test(
            "systemctl", "is-enabled", "--quiet", "qemu-guest-agent", codes=(0, 1, 3, 4)
        )
    elif task.id == "no-sleep":
        okay = all(
            ctx.run("systemctl", "is-enabled", f"{name}.target", allowed=(0, 1)).stdout.strip()
            == "masked"
            for name in TARGETS
        )
    else:
        okay = ctx.test("systemctl", "is-active", "--quiet", "ssh", codes=(0, 1, 3, 4))
    return ctx.result("satisfied" if okay else "pending")


def apply(ctx, task):
    if task.id == "upgrade":
        ctx.run("apt-get", "update", privileged=True)
        ctx.run(
            "apt-get",
            "-o",
            "Dpkg::Options::=--force-confold",
            "full-upgrade",
            "--no-remove",
            "-y",
            privileged=True,
        )
        ctx.warnings.append("A reboot may be needed to finish the upgrade")
    elif task.id == "guest-agent":
        apt(ctx, "qemu-guest-agent", "spice-vdagent")
        ctx.run("systemctl", "enable", "--now", "qemu-guest-agent", privileged=True)
        ctx.warnings.append("Enable QEMU Guest Agent in the Proxmox VM options")
    elif task.id == "no-sleep":
        ctx.run("systemctl", "mask", *(f"{name}.target" for name in TARGETS), privileged=True)
    else:
        apt(ctx, "openssh-server")
        ctx.run("systemctl", "enable", "--now", "ssh", privileged=True)
    return ctx.result("changed")
