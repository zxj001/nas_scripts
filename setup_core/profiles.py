"""Declarative profile policy and backward-compatible selection groups."""

from setup_core.model import Task


def registry(profile):
    linux = profile in {"debian", "proxmox"}
    admin = ("admin",) if linux else ("brew",)
    package = ("admin", "packages") if linux else ("brew",)
    if profile == "proxmox":
        # Every apt update fails with 401 until the enterprise repos are off.
        package += ("apt-repos",)
    tasks = []

    def add(name, module, needs=(), provides=(), resources=(), default=True, interactive=False):
        tasks.append(Task(name, module, needs, provides, resources, default, interactive))

    if profile == "macos":
        add("brew", "bootstrap", provides=("brew",), interactive=True)
    if profile == "debian":
        add("sudo", "bootstrap", provides=("admin",), interactive=True)
        add("upgrade", "system", ("admin", "packages"), resources=("packages",))
        add("guest-agent", "system", package, resources=("packages", "services"))
        add("no-sleep", "system", ("admin",), resources=("services",))
    if profile == "proxmox":
        add("repos", "repos", ("admin",), ("apt-repos",), ("packages",))
    if profile != "proxmox":
        add("power-restore", "power", ("admin",), default=False)
    # Small shared capability providers, also available with --with-deps.
    add(
        "download-tools",
        "packages",
        package if linux else (),
        ("https",),
        ("packages",) if linux else (),
    )
    if profile != "proxmox":
        add("git", "packages", package, ("git",), ("packages",))
        add("build-tools", "packages", package, resources=("packages",))
        add("dev-utilities", "packages", package, resources=("packages",))
        add("python", "packages", package, resources=("packages",))
        add("directories", "packages")
        add("setup-command", "launcher")
    if linux:
        if profile == "debian":
            add("ssh", "system", package, ("sshd",), ("packages", "ssh"))
        add(
            "ssh-keys",
            "ssh_keys",
            provides=("operator-key",),
            resources=("ssh-keys",),
            interactive=True,
        )
        add("ssh-harden", "ssh", ("admin", "sshd", "operator-key"), resources=("ssh",))
    add(
        "tailscale.install",
        "tailscale",
        ("https", *(package if linux else admin)),
        ("tailscale",),
        ("packages",),
    )
    add(
        "tailscale.login",
        "tailscale",
        ("tailscale", *(() if profile == "macos" else ("admin",))),
        ("tailnet",),
        ("tailscale",),
        interactive=True,
    )
    if profile == "proxmox":
        add(
            "subnet-router",
            "routing",
            ("admin", "tailnet"),
            resources=("tailscale", "forwarding"),
            default=False,
        )
    else:
        add("gh", "apps", package + (("https",) if linux else ()), resources=("packages",))
        add("node", "node", ("https",), ("node",), ("node",))
        add("codex", "apps", ("https",) if linux else ("brew",))
        add("pi", "apps", ("node",))
        add("claude", "apps", ("https",) if linux else ("brew",), ("claude",))
        add("herdr.install", "apps", ("https",), ("herdr",))
        add("herdr.integration", "apps", ("herdr", "claude"), default=False)
        add("firstmate", "repository", ("git",))
        add("docker", "docker", package, resources=("packages",))
        add("chromium", "browser", package, resources=("packages",))
        if profile == "debian":
            add("gpu", "gpu", package, resources=("packages",))
    if linux:
        # Manual until the iPhone app installs Shell Integration.
        add("shellfish", "shellfish", package, resources=("packages",))
    aliases = {"tailscale": ("tailscale.install", "tailscale.login")}
    if profile != "proxmox":
        aliases.update(
            {
                "dev-tools": (
                    "download-tools",
                    "git",
                    "build-tools",
                    "dev-utilities",
                    "python",
                    "directories",
                ),
                "herdr": ("herdr.install", "herdr.integration"),
            }
        )
    return tasks, aliases
