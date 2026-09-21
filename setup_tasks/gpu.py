"""Optional NVIDIA driver installation without replacing apt sources."""

from setup_core.files import Change, ManualConfig
from setup_tasks.common import apt

COMPONENTS = ("contrib", "non-free", "non-free-firmware")


def probe(ctx, task):
    if not ctx.have("lspci"):
        return ctx.result(
            "manual", "GPU discovery needs lspci", "Install pciutils to inspect hardware"
        )
    cards = ctx.run("lspci", "-nn").stdout.lower()
    if not any("nvidia" in line and ("vga" in line or "3d" in line) for line in cards.splitlines()):
        return ctx.result(
            "not-applicable", "no NVIDIA display device; VM passthrough is a host task"
        )
    if ctx.have("nvidia-smi") and ctx.test("nvidia-smi"):
        return ctx.result("satisfied")
    return ctx.result("pending")


def apply(ctx, task):
    paths = [
        ctx.path("/etc/apt/sources.list"),
        *ctx.path("/etc/apt/sources.list.d").glob("*.sources"),
        *ctx.path("/etc/apt/sources.list.d").glob("*.list"),
    ]
    content = "\n".join(
        line.split("#", 1)[0]
        for path in paths
        if path.is_file()
        for line in path.read_text().splitlines()
    )
    missing = [component for component in COMPONENTS if component not in content.split()]
    if missing:
        source = "".join(
            f"Types: deb\nURIs: {uri}\nSuites: {suites}\nComponents: {' '.join(missing)}\n\n"
            for uri, suites in [
                ("http://deb.debian.org/debian", "trixie trixie-updates"),
                ("http://security.debian.org/debian-security", "trixie-security"),
            ]
        )
        try:
            Change(ctx, ctx.path("/etc/apt/sources.list.d/nonfree.sources"), source).commit()
        except ManualConfig as error:
            return ctx.result("manual", str(error), "Reconcile apt components and retry")
    apt(ctx, "nvidia-driver", "firmware-misc-nonfree")
    return ctx.result(
        "deferred",
        "driver installed; reboot needed to load it",
        "Reboot, check nvidia-smi, then resume",
    )
