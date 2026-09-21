"""Small shared package/download mechanics, never step orchestration."""

import tempfile
from pathlib import Path

from setup_core.files import append_once


def apt(ctx, *packages):
    ctx.run("apt-get", "update", privileged=True)
    ctx.run(
        "apt-get",
        "-o",
        "Dpkg::Options::=--force-confold",
        "install",
        "--no-remove",
        "-y",
        *packages,
        privileged=True,
    )


def installer(ctx, url, interpreter="sh", interactive=False):
    # curl's retry applies to the download only. Never replay an installer.
    with tempfile.TemporaryDirectory(prefix="nas-installer-") as directory:
        target = Path(directory) / "install.sh"
        ctx.run(
            "curl",
            "-fSL",
            "--retry",
            "2",
            "--connect-timeout",
            "15",
            "--max-time",
            "180",
            url,
            "-o",
            target,
            timeout=600,
        )
        ctx.run(interpreter, target, interactive=interactive)


def local_path(ctx):
    profile = ctx.home / (".zprofile" if ctx.profile == "macos" else ".bashrc")
    append_once(profile, 'export PATH="$HOME/.local/bin:$PATH"')


def can_interact(ctx):
    return ctx.inputs.get("interactive") == "1"
