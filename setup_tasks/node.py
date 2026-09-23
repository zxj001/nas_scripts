"""nvm is isolated to a shell; consumers rediscover Node paths explicitly."""

from importlib.resources import as_file, files
from pathlib import Path

from setup_core.capabilities import available
from setup_tasks.common import installer


def probe(ctx, task):
    return ctx.result("satisfied" if available(ctx, "node") else "pending")


def apply(ctx, task):
    directory = Path(ctx.env["NVM_DIR"])
    if not (directory / "nvm.sh").is_file():
        if directory.exists() or directory.is_symlink():
            return ctx.result(
                "manual",
                f"preserving incomplete nvm directory: {directory}",
                "Inspect/repair nvm and retry",
            )
        directory.mkdir(parents=True)
        ctx.env["PROFILE"] = "/dev/null"
        installer(ctx, "https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.7/install.sh", "bash")
    with as_file(files("setup_adapters").joinpath("node.sh")) as adapter:
        ctx.run("bash", adapter)
    from setup_core.files import append_once

    profile = ctx.home / (".zprofile" if ctx.profile == "macos" else ".bashrc")
    import shlex

    append_once(profile, "export NVM_DIR=" + shlex.quote(str(directory)))
    append_once(profile, '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"')
    return ctx.result("changed")
