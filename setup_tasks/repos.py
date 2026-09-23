"""Proxmox apt repositories; the proven shell parser does the reading/writing."""

import re
from importlib.resources import as_file, files


def adapter(ctx, operation, allowed=(0,)):
    with as_file(files("setup_adapters").joinpath("pve_repos.sh")) as script:
        return ctx.run("bash", script, operation, allowed=allowed)


def configured(ctx):
    return adapter(ctx, "probe", allowed=(0, 1)).returncode == 0


def subscribed(ctx):
    # With an active subscription the enterprise repositories work; the
    # operator re-enabled them on purpose and apt update succeeds.
    if not ctx.have("pvesubscription"):
        return False
    output = ctx.run(
        "pvesubscription", "get", allowed=tuple(range(256)), quiet=True, split=True
    ).stdout
    return re.search(r"^status:\s*active\s*$", output, re.I | re.M) is not None


def usable(ctx):
    """apt update works: no-subscription configured, or a subscription is active."""
    return configured(ctx) or subscribed(ctx)


def probe(ctx, task):
    if configured(ctx):
        return ctx.result("satisfied")
    if subscribed(ctx):
        return ctx.result("satisfied", "active subscription; enterprise repositories kept")
    return ctx.result("pending", "enterprise repositories enabled or pve-no-subscription missing")


def apply(ctx, task):
    # Refusals (protected files, conflicting Signed-By) exit nonzero with the
    # reason, which the command boundary reports as the task failure.
    adapter(ctx, "apply")
    ctx.warnings.append(
        "pve-no-subscription is not recommended for production by Proxmox; "
        "with a subscription, re-enable the enterprise repositories"
    )
    return ctx.result("changed")
