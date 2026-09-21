"""Validate effective SSH policy, preserving operator config and access."""

from setup_core.capabilities import available
from setup_core.files import Change, ManualConfig, recovery_path
from setup_tasks.ssh_keys import operator_login_seen, valid_operator


def policy(ctx):
    args = ("-C", "user=root,host=localhost,addr=127.0.0.1") if ctx.profile == "proxmox" else ()
    output = ctx.run("/usr/sbin/sshd", "-T", *args, privileged=True).stdout
    return dict(line.split(None, 1) for line in output.splitlines() if " " in line)


def desired(ctx):
    values = {
        "permitrootlogin": "prohibit-password" if ctx.profile == "proxmox" else "no",
        "pubkeyauthentication": "yes",
        "passwordauthentication": "no",
    }
    if ctx.profile == "proxmox":
        values["kbdinteractiveauthentication"] = "no"
    return values


def policy_ok(ctx):
    actual = policy(ctx)
    if actual.get("permitrootlogin") == "without-password":
        actual["permitrootlogin"] = "prohibit-password"
    return all(actual.get(key) == value for key, value in desired(ctx).items())


def probe(ctx, task):
    if not available(ctx, "admin") or not available(ctx, "sshd"):
        return ctx.result("pending", "SSH inspection needs sshd and privilege")
    if recovery_path(ctx.path("/etc/ssh/sshd_config.d/99-local.conf")).exists():
        return ctx.result("pending", "interrupted SSH configuration needs validation and reload")
    if policy_ok(ctx):
        return ctx.result(
            "satisfied",
            "effective policy verified",
            "Verify a new key session; user/address-specific Match policies are not exhaustively checked",
        )
    config = ctx.path("/etc/ssh/sshd_config.d/99-local.conf")
    if config.exists() or config.is_symlink():
        return ctx.result(
            "manual",
            f"SSH hardening skipped; preserving {config}",
            "Review sudo /usr/sbin/sshd -T; remaining setup can continue",
        )
    return ctx.result("pending")


def apply(ctx, task):
    if not valid_operator(ctx):
        return ctx.result("blocked", "a valid operator key is required before disabling passwords")
    if ctx.profile == "proxmox":
        if not operator_login_seen(ctx):
            return ctx.result(
                "deferred",
                "no successful operator root-key login found in ssh.service journal",
                "SSH in as root with that key from another terminal, then resume",
            )
    elif ctx.user == "root":
        return ctx.result("manual", "Debian hardening requires a non-root operator account")
    config = ctx.path("/etc/ssh/sshd_config.d/99-local.conf")
    content = "".join(f"{key} {value}\n" for key, value in desired(ctx).items())
    try:
        change = Change(ctx, config, content)
    except ManualConfig as error:
        return ctx.result("manual", str(error), "Review SSH settings; remaining setup can continue")
    try:
        ctx.run("/usr/sbin/sshd", "-t", privileged=True)
        if not policy_ok(ctx):
            raise RuntimeError("earlier configuration overrides the desired SSH policy")
        ctx.run("systemctl", "reload", "ssh", privileged=True)
    except Exception as error:  # Task boundary must report any failure.
        try:
            change.rollback()
            ctx.run("systemctl", "reload", "ssh", privileged=True)
        except Exception as rollback:  # Rollback must report any failure.
            return ctx.result(
                "failed",
                f"{error}; rollback failed: {rollback}",
                "Repair SSH before retrying",
                unsafe=("ssh",),
            )
        return ctx.result(
            "failed", f"{error}; previous configuration restored", "Review SSH settings and retry"
        )
    change.commit()
    ctx.warnings.append("Verify a new key-based SSH session before closing the current session")
    return ctx.result("changed")
