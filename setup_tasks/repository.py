"""Never pull/reset or replace an operator checkout during setup."""


def probe(ctx, task):
    target = ctx.home / "tools/firstmate"
    if (target / ".git").exists():
        return ctx.result("satisfied")
    if target.exists() or target.is_symlink():
        return ctx.result(
            "manual",
            f"preserving existing destination: {target}",
            "Choose or repair the checkout manually",
        )
    return ctx.result("pending")


def apply(ctx, task):
    target = ctx.home / "tools/firstmate"
    target.parent.mkdir(parents=True, exist_ok=True)
    ctx.run("git", "clone", "https://github.com/kunchenguid/firstmate", target)
    return ctx.result("changed")
