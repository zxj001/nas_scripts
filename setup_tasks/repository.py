"""Never pull/reset or replace an operator checkout during setup."""

from pathlib import Path


def destination(ctx):
    # --firstmate-dir, already absolute; ~/firstmate by default.
    return Path(ctx.inputs.get("firstmate_dir") or ctx.home / "firstmate")


def probe(ctx, task):
    target = destination(ctx)
    if (target / ".git").exists():
        return ctx.result("satisfied")
    if target.exists() or target.is_symlink():
        return ctx.result(
            "manual",
            f"preserving existing destination: {target}",
            "Choose another --firstmate-dir or repair the checkout manually",
        )
    return ctx.result("pending")


def apply(ctx, task):
    target = destination(ctx)
    target.parent.mkdir(parents=True, exist_ok=True)
    ctx.run("git", "clone", "https://github.com/kunchenguid/firstmate", target)
    return ctx.result("changed")
