"""Installing Tailscale does not require completing interactive authentication."""

from setup_tasks.common import can_interact, installer

# The macOS app ships its CLI inside the bundle; it is often not on PATH.
MAC_APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def command(ctx):
    if ctx.have("tailscale"):
        return "tailscale"
    if ctx.profile == "macos" and ctx.path(MAC_APP_CLI).exists():
        return str(ctx.path(MAC_APP_CLI))
    return None


def connected(ctx):
    cli = command(ctx)
    return bool(cli) and ctx.test(cli, "status", codes=tuple(range(256)))


def probe(ctx, task):
    if task.id.endswith("install"):
        installed = ctx.have("tailscale") or (
            ctx.profile == "macos" and ctx.path("/Applications/Tailscale.app").exists()
        )
        return ctx.result("satisfied" if installed else "pending")
    if connected(ctx):
        return ctx.result("satisfied")
    if ctx.profile == "macos":
        # The app signs in; no sudo and no tailscale up on a Mac.
        return ctx.result("manual", "Tailscale is not connected", "Open Tailscale.app and sign in")
    return ctx.result("pending")


def apply(ctx, task):
    if task.id.endswith("install"):
        if ctx.profile == "macos":
            ctx.run("brew", "install", "--cask", "tailscale")
        else:
            installer(ctx, "https://tailscale.com/install.sh")
        return ctx.result("changed")
    if ctx.profile == "macos":
        return probe(ctx, task)
    if not can_interact(ctx):
        return ctx.result(
            "deferred",
            "Tailscale authentication needs operator input",
            "Run sudo tailscale up, then resume",
        )
    ctx.run("tailscale", "up", privileged=True, interactive=True)
    return ctx.result("changed")
