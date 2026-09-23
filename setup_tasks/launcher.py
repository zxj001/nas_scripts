"""Install a reusable command without replacing an operator's launcher."""

import hashlib
import os
import shlex
import sys
import tempfile
from pathlib import Path

from setup_core.distribution import bundle_bytes


def probe(ctx, task):
    target = ctx.home / ".local/bin/setup-machine"
    if target.exists() or target.is_symlink():
        return ctx.result(
            "satisfied",
            "existing setup-machine command preserved",
            "Updates are explicit: use the desired checkout or release wrapper",
        )
    return ctx.result("pending")


def publish(path, content, mode):
    """Complete and executable before it appears; never replaces an existing file."""
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.link(name, path)
    finally:
        os.unlink(name)


def apply(ctx, task):
    root = Path(__file__).resolve().parent.parent
    content = bundle_bytes(root)
    digest = hashlib.sha256(content).hexdigest()
    cache = ctx.home / ".local/share/nas-setup/bundles" / digest
    from setup_core.state import private_directory

    private_directory(cache)
    bundle = cache / "setup.pyz"
    if bundle.exists() and (bundle.is_symlink() or bundle.read_bytes() != content):
        return ctx.result("manual", "existing cached bundle differs; preserving it")
    if not bundle.exists():
        publish(bundle, content, 0o600)
    launcher = ctx.home / ".local/bin/setup-machine"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    if launcher.exists() or launcher.is_symlink():
        return ctx.result("satisfied", "existing launcher preserved")
    script = (
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " -I "
        + shlex.quote(str(bundle))
        + ' --profile auto "$@"\n'
    )
    publish(launcher, script.encode(), 0o755)
    return ctx.result("changed", "installed setup-machine using a complete local bundle")
