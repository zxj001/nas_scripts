"""ShellFish widget: cron pushes name, CPU, temperature, memory and disk usage.

The iPhone app writes ~/.shellfishrc when Shell Integration is installed; until
then the task is manual. The widget script ships in this bundle and is copied to
a stable path, so cron never depends on a checkout. Its widget function needs
openssl, xxd and curl: without xxd it exits 0 but sends an unreadable payload.
"""

import os
import re
import shlex
import socket
import tempfile
from importlib.resources import files

from setup_tasks.common import apt

TOOLS = ("openssl", "xxd", "curl", "crontab")
# First line of our script; anything else at the target is an operator file.
MARK = "# shellfish_widget.sh - "
# Earlier setup versions ran the widget from a repository checkout. Such a line,
# including one the operator gave mount points, --name or --target, is kept and
# pointed at the installed copy, so its arguments survive.
OLD_PATH = re.compile(r"(?<=\s)\S*/scripts/shellfish_widget\.sh(?=\s|$)")


def target(ctx):
    if ctx.profile == "proxmox":
        return ctx.path("/usr/local/bin/shellfish_widget.sh")
    return ctx.home / ".local/bin/shellfish_widget.sh"


def bundled():
    return files("setup_adapters").joinpath("shellfish_widget.sh").read_text()


def ours(path):
    lines = path.read_text(errors="replace").splitlines()[:3]
    return any(line.startswith(MARK) for line in lines)


def widget_target(ctx):
    """The widget this machine sends to: --widget-target, else the short
    hostname. Empty means the one widget every machine shares."""
    value = ctx.inputs.get("widget_target")
    if value is None:
        value = socket.gethostname().split(".")[0]
    return value


def cron_line(path, target=""):
    option = f" --target {shlex.quote(target)}" if target else ""
    return f"*/15 * * * * {shlex.quote(str(path))}{option} >/dev/null 2>&1"


def runs(line, path):
    return not line.lstrip().startswith("#") and str(path) in line


def untargeted(line, path):
    # A line the operator already pointed at a widget keeps its target.
    return runs(line, path) and "--target" not in line.split()


def with_target(line, path, target):
    if not target or not untargeted(line, path):
        return line
    quoted = shlex.quote(str(path))
    word = quoted if quoted in line else str(path)
    at = line.index(word) + len(word)
    return line[:at] + " --target " + shlex.quote(target) + line[at:]


def read_crontab(ctx):
    # stderr is never crontab content, and the crontab is not echoed.
    output = ctx.run("crontab", "-l", allowed=(0, 1), quiet=True, split=True)
    if output.returncode == 0:
        return output.stdout
    if "no crontab for" in output.stderr:
        return ""
    # An unreadable crontab is not an empty one; never overwrite it.
    raise RuntimeError("cannot read crontab: " + output.stderr.strip())


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
    lines = read_crontab(ctx).splitlines()
    if not any(runs(line, path) for line in lines):
        return ctx.result("pending", "no crontab entry runs the widget")
    if widget_target(ctx) and any(untargeted(line, path) for line in lines):
        return ctx.result("pending", f"widget line has no --target {widget_target(ctx)}")
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
    quoted = shlex.quote(str(path))
    lines = [
        line if line.lstrip().startswith("#") else OLD_PATH.sub(lambda _: quoted, line)
        for line in before.splitlines()
    ]
    widget = widget_target(ctx)
    lines = [with_target(line, path, widget) for line in lines]
    if not any(runs(line, path) for line in lines):
        lines.append(cron_line(path, widget))
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
