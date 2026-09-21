"""Proxmox subnet routing preserves existing advertisements and sysctl settings."""

import ipaddress
import json
import re

from setup_core.capabilities import available
from setup_core.files import Change, recovery_path


def route(ctx):
    subnet = ipaddress.ip_network(ctx.inputs.get("lan_route", "192.168.1.0/24"))
    if subnet.version != 4 or subnet.prefixlen == 0:
        raise ValueError("subnet-router requires an IPv4 subnet, not a default route")
    return str(subnet)


def advertised(ctx):
    return json.loads(ctx.run("tailscale", "debug", "prefs").stdout).get("AdvertiseRoutes") or []


def probe(ctx, task):
    if not available(ctx, "tailnet") or not available(ctx, "admin"):
        return ctx.result("pending")
    config = ctx.path("/etc/sysctl.d/99-tailscale.conf")
    if recovery_path(config).exists():
        return ctx.result("pending", "interrupted forwarding configuration needs reconciliation")
    persistent = config.exists() and re.search(
        r"^\s*net\.ipv4\.ip_forward\s*=\s*1\s*$", config.read_text(), re.MULTILINE
    )
    live = ctx.run("sysctl", "-n", "net.ipv4.ip_forward").stdout.strip() == "1"
    okay = bool(persistent and live and route(ctx) in advertised(ctx))
    if okay:
        ctx.warnings.append(
            "Route advertisement is local; approve/check this route in the Tailscale admin console"
        )
    return ctx.result("satisfied" if okay else "pending")


def apply(ctx, task):
    ctx.run("tailscale", "set", "--help")
    routes = [r for r in advertised(ctx) if r not in ("0.0.0.0/0", "::/0")]
    routes = list(dict.fromkeys([*routes, route(ctx)]))
    config = ctx.path("/etc/sysctl.d/99-tailscale.conf")
    before = ctx.read(config, privileged=True) if config.exists() else ""
    lines = [
        line for line in before.splitlines() if not re.match(r"^\s*net\.ipv4\.ip_forward\s*=", line)
    ]
    prior = ctx.run("sysctl", "-n", "net.ipv4.ip_forward").stdout.strip()
    if prior not in ("0", "1"):
        raise ValueError("unexpected current forwarding state")
    change = Change(ctx, config, "\n".join([*lines, "net.ipv4.ip_forward = 1", ""]), replace=True)
    try:
        ctx.run("sysctl", "-w", "net.ipv4.ip_forward=1", privileged=True)
        ctx.run("tailscale", "set", "--advertise-routes=" + ",".join(routes), privileged=True)
    except Exception as error:
        try:
            change.rollback()
            ctx.run("sysctl", "-w", "net.ipv4.ip_forward=" + prior, privileged=True)
        except Exception as rollback:  # Rollback must report any failure.
            return ctx.result(
                "failed", f"{error}; rollback failed: {rollback}", unsafe=("forwarding",)
            )
        raise
    change.commit()
    ctx.warnings.append(
        "Approve the advertised subnet in the Tailscale admin console; approval is not verified locally"
    )
    return ctx.result("changed")
