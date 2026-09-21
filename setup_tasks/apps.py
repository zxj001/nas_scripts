"""Agent CLIs and optional integration are separate verifiable tasks."""

import re

from setup_core.files import Change, ManualConfig
from setup_tasks.common import apt, installer, local_path

INSTALLERS = {
    "codex": ("https://chatgpt.com/codex/install.sh", "sh"),
    "claude": ("https://claude.ai/install.sh", "bash"),
    "herdr.install": ("https://herdr.dev/install.sh", "sh"),
}


def probe(ctx, task):
    if task.id == "herdr.integration":
        if not ctx.have("herdr") or not ctx.have("claude"):
            return ctx.result("pending")
        status = ctx.run("herdr", "integration", "status").stdout
        if re.search(r"^claude: current\b", status, re.MULTILINE):
            return ctx.result("satisfied")
        settings = ctx.home / ".claude/settings.json"
        if settings.exists() or settings.is_symlink():
            return ctx.result(
                "manual",
                "preserving existing Claude settings",
                "Review hooks, then run herdr integration install claude",
            )
        return ctx.result("pending")
    command = "herdr" if task.id == "herdr.install" else task.id
    return ctx.result("satisfied" if ctx.have(command) else "pending")


def apply(ctx, task):
    if task.id == "herdr.integration":
        # Recheck immediately before handing fresh settings to the vendor CLI.
        existing = probe(ctx, task)
        if existing.outcome != "pending":
            return existing
        ctx.run("herdr", "integration", "install", "claude")
    elif task.id == "pi":
        ctx.run("npm", "install", "-g", "--ignore-scripts", "@earendil-works/pi-coding-agent")
    elif task.id == "gh" and ctx.profile != "macos":
        import tempfile
        from pathlib import Path

        keyring = ctx.path("/etc/apt/keyrings/githubcli-archive-keyring.gpg")
        if not keyring.exists() and not keyring.is_symlink():
            with tempfile.TemporaryDirectory() as directory:
                key = Path(directory) / "key"
                ctx.run(
                    "curl",
                    "-fSL",
                    "--retry",
                    "2",
                    "https://cli.github.com/packages/githubcli-archive-keyring.gpg",
                    "-o",
                    key,
                    timeout=180,
                )
                # The key endpoint is binary OpenPGP; publish bytes through helper.
                import base64
                import sys

                from setup_core.files import PROGRAM

                ctx.run(
                    sys.executable,
                    "-I",
                    "-c",
                    PROGRAM,
                    "create",
                    keyring,
                    base64.b64encode(key.read_bytes()).decode(),
                    privileged=True,
                )
                ctx.run(sys.executable, "-I", "-c", PROGRAM, "commit", keyring, "", privileged=True)
        arch = ctx.run("dpkg", "--print-architecture").stdout.strip()
        source = (
            f"deb [arch={arch} signed-by={keyring}] https://cli.github.com/packages stable main\n"
        )
        try:
            Change(ctx, ctx.path("/etc/apt/sources.list.d/github-cli.list"), source).commit()
        except ManualConfig as error:
            return ctx.result("manual", str(error), "Reconcile the apt source and retry")
        apt(ctx, "gh")
    elif ctx.profile == "macos" and task.id in {"gh", "codex", "claude"}:
        package = "claude-code" if task.id == "claude" else task.id
        ctx.run("brew", "install", *(() if task.id == "gh" else ("--cask",)), package)
    elif task.id in INSTALLERS:
        installer(ctx, *INSTALLERS[task.id])
        local_path(ctx)
    else:
        return ctx.result("manual", "integration requires operator review")
    if task.id in {"gh", "pi", "codex", "claude"}:
        ctx.warnings.append("Authentication may still be needed; run the CLI to sign in")
    return ctx.result("changed")
