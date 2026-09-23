"""ShellFish widget: cron pushes name, CPU, temperature, memory and disk usage.

The iPhone app writes ~/.shellfishrc when Shell Integration is installed; until
then the task is manual. The widget script ships in this bundle and is copied to
a stable path, so cron never depends on a checkout. Its widget function needs
openssl, xxd and curl: without xxd it exits 0 but sends an unreadable payload.
"""

import os
import re
import shlex
import tempfile
from importlib.resources import files

from setup_tasks.common import apt

TOOLS = ("openssl", "xxd", "curl", "crontab")
# First line of our script; anything else at the target is an operator file.
MARK = "# shellfish_widget.sh - "
# The line earlier setup versions added, pointing into a repository checkout.
OLD_LINE = re.compile(r"^\*/15 \* \* \* \* \S*/scripts/shellfish_widget\.sh >/dev/null 2>&1$")


def target(ctx):
    if ctx.profile == "proxmox":
        return ctx.path("/usr/local/bin/shellfish_widget.sh")
    return ctx.home / ".local/bin/shellfish_widget.sh"


def bundled():
    return files("setup_adapters").joinpath("shellfish_widget.sh").read_text()


def ours(path):
    lines = path.read_text(errors="replace").splitlines()[:3]
    return any(line.startswith(MARK) for line in lines)


def cron_line(path):
    return f"*/15 * * * * {shlex.quote(str(path))} >/dev/null 2>&1"


def read_crontab(ctx):
    # stderr is merged into stdout at the command boundary.
    output = ctx.run("crontab", "-l", allowed=(0, 1))
    if output.returncode == 0:
        return output.stdout
    if "no crontab for" in output.stdout:
        return ""
    # An unreadable crontab is not an empty one; never overwrite it.
    raise RuntimeError("cannot read crontab: " + output.stdout.strip())


def integration_missing(ctx):
    if (ctx.home / ".shellfishrc").exists():
        return None
    who = "root" if ctx.profile == "proxmox" else ctx.user
    return ctx.result(
        "manual",
        "ShellFish Shell Integration is not installed (~/.shellfishrc)",
        f"In ShellFish, connected as {who}: server settings -> Install Shell Integration, "
        "then rerun --only shellfish",
    )


def probe(ctx, task):
    if ctx.profile == "macos":
        return ctx.result("not-applicable", "the widget script reads Linux /proc")
    missing = integration_missing(ctx)
    if missing:
        return missing
    path = target(ctx)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or not ours(path):
            return ctx.result(
                "manual",
                f"preserving existing {path}",
                "Move it aside, then rerun --only shellfish",
            )
        if path.read_text() != bundled() or not os.access(path, os.X_OK):
            return ctx.result("pending", "installed widget script differs from this version")
    else:
        return ctx.result("pending", "widget script not installed")
    if not all(ctx.have(tool) for tool in TOOLS):
        return ctx.result("pending", "openssl, xxd, curl or cron is missing")
    if not any(str(path) in line for line in read_crontab(ctx).splitlines()):
        return ctx.result("pending", "no crontab entry runs the widget")
    return ctx.result("satisfied")


def install(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        os.chmod(name, 0o755)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def apply(ctx, task):
    current = probe(ctx, task)
    if current.outcome != "pending":
        return current
    if not all(ctx.have(tool) for tool in TOOLS):
        apt(ctx, "openssl", "xxd", "curl", "cron")
    path = target(ctx)
    content = bundled()
    if not path.exists() or path.read_text() != content or not os.access(path, os.X_OK):
        install(path, content)
    before = read_crontab(ctx)
    lines = [line for line in before.splitlines() if not OLD_LINE.match(line)]
    if not any(str(path) in line for line in lines):
        lines.append(cron_line(path))
    others = [line for line in lines if "shellfish_widget.sh" in line and str(path) not in line]
    if others:
        ctx.warnings.append("other crontab lines also run a widget script: " + "; ".join(others))
    after = "\n".join(lines) + "\n"
    if after != before:
        with tempfile.NamedTemporaryFile("w", prefix="nas-crontab-", delete=False) as stream:
            stream.write(after)
        try:
            ctx.run("crontab", stream.name)
        finally:
            os.unlink(stream.name)
    ctx.run(str(path))
    ctx.warnings.append("Widget sent; add a ShellFish widget on the iPhone if you have not")
    return ctx.result("changed")
