"""Cohesive capability providers behind the dev-tools compatibility group."""

from setup_core.capabilities import available
from setup_tasks.common import apt

COMMANDS = {"git": ("git",), "build-tools": ("make", "gcc", "g++"), "dev-utilities": ("jq", "rg")}
PACKAGES = {
    "download-tools": ("curl", "ca-certificates"),
    # pip plus venv: Debian's python3 refuses system-wide pip installs
    # (PEP 668), so pip is only usable in a venv, and python3-venv brings ensurepip.
    "python": ("python3", "python3-pip", "python3-venv"),
    "git": ("git",),
    "build-tools": ("build-essential",),
    "dev-utilities": ("jq", "ripgrep"),
}


def probe(ctx, task):
    if task.id == "directories":
        okay = all((ctx.home / name).is_dir() for name in ("projects", "tools"))
    elif task.id == "download-tools":
        okay = available(ctx, "https")
    elif task.id == "python":
        okay = (
            ctx.have("python3")
            and ctx.test("python3", "-m", "pip", "--version")
            and (ctx.profile == "macos" or ctx.test("python3", "-c", "import ensurepip, venv"))
        )
    else:
        okay = all(ctx.have(name) for name in COMMANDS[task.id])
    return ctx.result("satisfied" if okay else "pending")


def apply(ctx, task):
    if task.id == "directories":
        for name in ("projects", "tools"):
            (ctx.home / name).mkdir(exist_ok=True)
    elif ctx.profile != "macos":
        apt(ctx, *PACKAGES[task.id])
    elif task.id == "dev-utilities":
        ctx.run("brew", "install", "jq", "ripgrep")
    elif task.id == "git":
        ctx.run("brew", "install", "git")
    elif task.id == "python":
        ctx.run("brew", "install", "python")
    else:
        return ctx.result(
            "manual",
            "macOS command-line prerequisites are missing",
            "Install Xcode Command Line Tools (xcode-select --install) and retry",
        )
    return ctx.result("changed")
