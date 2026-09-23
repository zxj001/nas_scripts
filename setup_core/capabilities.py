"""Probe actual prerequisites; failures are not inferred from provider outcomes."""

import os
import re


def available(ctx, name):
    if name == "admin":
        return os.geteuid() == 0 or (ctx.have("sudo") and ctx.test("sudo", "-n", "true"))
    if name == "packages":
        return (
            ctx.have("dpkg")
            and ctx.run("dpkg", "--audit", privileged=True, split=True).stdout.strip() == ""
        )
    if name == "https":
        if not ctx.have("curl"):
            return False
        if ctx.profile == "macos":
            return True
        return (
            ctx.run(
                "dpkg-query", "-W", "-f=${Status}", "ca-certificates", allowed=(0, 1), split=True
            ).stdout
            == "install ok installed"
        )
    if name == "node":
        if not ctx.have("node") or not ctx.have("npm"):
            return False
        version = ctx.run("node", "--version").stdout.strip()
        match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
        return bool(match and tuple(map(int, match.groups())) >= (22, 19, 0))
    if name == "sshd":
        return ctx.path("/usr/sbin/sshd").is_file()
    if name == "operator-key":
        from setup_tasks.ssh_keys import valid_operator

        return valid_operator(ctx)
    if name == "apt-repos":
        from setup_tasks.repos import usable

        return usable(ctx)
    if name == "tailnet":
        from setup_tasks.tailscale import connected

        return connected(ctx)
    if name == "tailscale":
        from setup_tasks.tailscale import command

        return command(ctx) is not None
    if name in {"git", "brew", "claude", "herdr"}:
        return ctx.have(name)
    raise ValueError(f"unknown capability: {name}")


def health(ctx, resource):
    if resource == "packages":
        return available(ctx, "packages") if ctx.profile != "macos" else True
    if resource == "ssh":
        return ctx.test("/usr/sbin/sshd", "-t", privileged=True, codes=(0, 1, 255))
    # Unknown partial mutation must not be treated as healthy automatically.
    return False
