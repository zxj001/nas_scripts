"""A Chromium-based browser that agents can drive headless."""

from setup_tasks.common import apt

# Homebrew disabled its unsigned chromium cask; Google Chrome is the signed
# Chromium build, and either app is accepted when already installed.
MAC_APPS = ("/Applications/Chromium.app", "/Applications/Google Chrome.app")


def probe(ctx, task):
    if ctx.profile == "macos":
        installed = any(ctx.path(app).exists() for app in MAC_APPS)
    else:
        installed = ctx.have("chromium")
    return ctx.result("satisfied" if installed else "pending")


def apply(ctx, task):
    if ctx.profile == "macos":
        ctx.run("brew", "install", "--cask", "google-chrome")
    else:
        apt(ctx, "chromium")
    return ctx.result("changed")
