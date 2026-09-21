"""Privilege and Homebrew bootstrap; re-login is a deferred capability."""

import grp
import os
from pathlib import Path

from setup_core.files import append_once
from setup_tasks.common import can_interact, installer


def probe(ctx, task):
    if task.id == "brew":
        return ctx.result("satisfied" if ctx.have("brew") else "pending")
    if os.geteuid() == 0 or (
        ctx.have("sudo") and "sudo" in [grp.getgrgid(g).gr_name for g in os.getgroups()]
    ):
        return ctx.result("satisfied")
    return ctx.result("pending")


def apply(ctx, task):
    if not can_interact(ctx):
        return ctx.result(
            "deferred", "interactive bootstrap requires a terminal", "Rerun interactively"
        )
    if task.id == "brew":
        installer(
            ctx,
            "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh",
            "/bin/bash",
            True,
        )
        brew = (
            "/opt/homebrew/bin/brew"
            if Path("/opt/homebrew/bin/brew").exists()
            else "/usr/local/bin/brew"
        )
        append_once(ctx.home / ".zprofile", f'eval "$("{brew}" shellenv)"')
        return ctx.result("changed")
    # shlex quotes the target user as data for su's one required command string.
    import shlex

    command = (
        "apt-get update && apt-get install -y sudo && /usr/sbin/usermod -aG sudo "
        + shlex.quote(ctx.user)
    )
    ctx.run("su", "-c", command, interactive=True)
    return ctx.result(
        "deferred",
        "sudo group membership changed; current login has not changed",
        "Log out and back in, then resume; independent user tasks can still run",
    )
