"""Proxmox apt repositories; the proven shell parser does the reading/writing."""

from importlib.resources import as_file, files


def adapter(ctx, operation, allowed=(0,)):
    with as_file(files("setup_adapters").joinpath("pve_repos.sh")) as script:
        return ctx.run("bash", script, operation, allowed=allowed)


def configured(ctx):
    return adapter(ctx, "probe", allowed=(0, 1)).returncode == 0


def probe(ctx, task):
    if configured(ctx):
        return ctx.result("satisfied")
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
