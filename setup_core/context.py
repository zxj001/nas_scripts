"""Explicit target identity, executable discovery and command access."""

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from setup_core.commands import execute
from setup_core.model import Result


@dataclass
class Context:
    profile: str
    home: Path
    user: str
    env: dict[str, str]
    phase: str = "probe"
    inputs: dict[str, str] = field(default_factory=dict)
    root: Path = Path("/")
    task_id: str = ""
    warnings: list[str] = field(default_factory=list)

    def path(self, name):
        return self.root / name.lstrip("/")

    def have(self, name):
        return shutil.which(name, path=self.env["PATH"]) is not None

    def run(
        self,
        *args,
        privileged=False,
        allowed=(0,),
        interactive=False,
        timeout=None,
        quiet=False,
        split=False,
    ):
        """quiet: never echo (file contents, journals, internal JSON).
        split: stdout only, stderr kept apart in .stderr."""
        if privileged and os.geteuid() != 0:
            args = ("sudo", "-n", *args)
        return execute(
            args,
            env=self.env,
            allowed=allowed,
            interactive=interactive,
            timeout=timeout if timeout is not None else (30 if self.phase != "apply" else None),
            emit=self.phase == "apply" and not quiet,
            split=split,
        )

    def read(self, path, *, privileged=False):
        if privileged:
            return self.run("cat", path, privileged=True, quiet=True, split=True).stdout
        return Path(path).read_text()

    def result(self, outcome, reason="", action="", unsafe=()):
        return Result(self.task_id, outcome, reason, action, list(self.warnings), list(unsafe))

    def test(self, *args, privileged=False, codes=(0, 1)):
        return self.run(*args, privileged=privileged, allowed=codes).returncode == 0

    def require_input(self, name, action):
        return self.inputs.get(name) or None


def environment(home, inherited):
    # No BASH_ENV, exported functions, PYTHONPATH or arbitrary installer knobs.
    env = {
        key: inherited[key]
        for key in (
            "TERM",
            "LANG",
            "LC_ALL",
            "SSH_AUTH_SOCK",
            "http_proxy",
            "https_proxy",
            "no_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
        )
        if key in inherited
    }
    env.update(HOME=str(home), LC_ALL="C", PYTHONUNBUFFERED="1")
    paths = [
        str(home / ".local/bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    # Discover installed compatible Node without sourcing nvm or changing aliases.
    nvm_dir = Path(inherited.get("NVM_DIR", str(home / ".nvm"))).expanduser()
    versions = []
    for entry in (nvm_dir / "versions/node").glob("v*/bin"):
        match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", entry.parent.name)
        if match:
            versions.append((tuple(map(int, match.groups())), str(entry)))
    for _, path in sorted(versions):
        paths.insert(0, path)
    # Keep nonstandard executable directories only when explicitly supplied by
    # the caller; tests instead inject a Context with a controlled PATH.
    paths.extend(inherited.get("PATH", "").split(os.pathsep))
    env["PATH"] = os.pathsep.join(dict.fromkeys(p for p in paths if p))
    env["NVM_DIR"] = str(nvm_dir)
    return env
