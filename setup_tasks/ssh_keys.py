"""Operator keys, distinct from Proxmox's node/cluster credentials."""

import tempfile
from pathlib import Path

from setup_core.files import append_once


def paths(ctx):
    directory = ctx.home / ".ssh"
    return directory / "authorized_keys", directory / "operator-keys"


def fingerprints(ctx, path):
    if not path.exists():
        return set()
    out = ctx.run("ssh-keygen", "-l", "-f", path, allowed=(0, 1, 255))
    return {
        line.split()[1]
        for line in out.stdout.splitlines()
        if len(line.split()) > 1 and line.split()[1].startswith("SHA256:")
    }


def key_fingerprints(ctx, key):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "key"
        path.write_text(key + "\n")
        return fingerprints(ctx, path)


def valid_operator(ctx):
    auth, recorded = paths(ctx)
    current = fingerprints(ctx, auth)
    supplied = ctx.inputs.get("ssh_key", "")
    if ctx.profile != "proxmox":
        return bool(current) and (not supplied or bool(key_fingerprints(ctx, supplied) & current))
    known = set(recorded.read_text().splitlines()) if recorded.exists() else set()
    if supplied:
        known &= key_fingerprints(ctx, supplied)
    return bool(current & known)


def operator_login_seen(ctx):
    auth, recorded = paths(ctx)
    known = set(recorded.read_text().splitlines()) if recorded.exists() else set()
    # Filtered at the source: a busy cluster's journal is large, oldest first,
    # and full of addresses and user names that must not be echoed or logged.
    journal = ctx.run(
        "journalctl",
        "-u",
        "ssh.service",
        "--no-pager",
        "-o",
        "cat",
        "--grep",
        "Accepted publickey for root ",
        allowed=(0, 1),
        timeout=120,
        quiet=True,
        split=True,
    ).stdout
    return any(
        "Accepted publickey for root " in line
        and any(fp in line for fp in known & fingerprints(ctx, auth))
        for line in journal.splitlines()
    )


def probe(ctx, task):
    return ctx.result("satisfied" if valid_operator(ctx) else "pending")


def apply(ctx, task):
    key = ctx.inputs.get("ssh_key", "").strip()
    if not key:
        return ctx.result(
            "deferred",
            "no operator public key supplied",
            "Pass --ssh-key or PVE_OPERATOR_KEY; never supply a private key",
        )
    # ssh-keygen -l also fingerprints a private key and skips junk lines, and
    # every line here would be appended to authorized_keys.
    lines = [line for line in key.splitlines() if line.strip() and not line.startswith("#")]
    if len(lines) != 1 or "PRIVATE KEY" in key:
        return ctx.result("failed", "not a single valid SSH public key")
    fps = key_fingerprints(ctx, key)
    if len(fps) != 1:
        return ctx.result("failed", "not a single valid SSH public key")
    auth, recorded = paths(ctx)
    auth.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    auth.parent.chmod(0o700)
    if not fps & fingerprints(ctx, auth):
        append_once(auth, key)
    if ctx.profile == "proxmox":
        append_once(recorded, next(iter(fps)))
    auth.chmod(0o600)
    return ctx.result("changed")
