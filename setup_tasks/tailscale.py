"""Installing Tailscale does not require completing interactive authentication."""

from setup_tasks.common import can_interact, installer


def probe(ctx, task):
    if task.id.endswith("install"):
        installed = ctx.have("tailscale") or (
            ctx.profile == "macos" and ctx.path("/Applications/Tailscale.app").exists()
        )
        return ctx.result("satisfied" if installed else "pending")
    if ctx.profile == "macos" and not ctx.have("tailscale"):
        return ctx.result(
            "manual", "use the Tailscale app to connect", "Open Tailscale.app and sign in"
        )
    if not ctx.have("tailscale"):
        return ctx.result("pending")
    return ctx.result("satisfied" if ctx.test("tailscale", "status") else "pending")


def apply(ctx, task):
    if task.id.endswith("install"):
        if ctx.profile == "macos":
            ctx.run("brew", "install", "--cask", "tailscale")
        else:
            installer(ctx, "https://tailscale.com/install.sh")
        return ctx.result("changed")
    if not can_interact(ctx):
        return ctx.result(
            "deferred",
            "Tailscale authentication needs operator input",
            "Run sudo tailscale up, then resume",
        )
    ctx.run("tailscale", "up", privileged=True, interactive=True)
    return ctx.result("changed")
