"""Task/file contracts use controlled command responses and fixture-only paths."""

import subprocess
import sys
from pathlib import Path

import pytest

from setup_core.commands import CommandError, Output, redact
from setup_core.context import Context
from setup_core.files import PROGRAM, Change, ManualConfig, append_once
from setup_core.model import Task
from setup_tasks import node, power, repository, ssh, tailscale


class Fixture(Context):
    def __init__(self, path, profile="debian", outputs=None, phase="probe"):
        super().__init__(
            profile,
            path / "home",
            "fixture",
            {
                "PATH": "/usr/bin:/bin",
                "HOME": str(path / "home"),
                "NVM_DIR": str(path / "home/.nvm"),
            },
            phase,
            root=path,
        )
        self.home.mkdir()
        self.outputs = outputs or {}
        self.calls = []
        self.task_id = "fixture"

    def have(self, name):
        return self.outputs.get(("have", name), False)

    def run(self, *args, **kwargs):
        args = tuple(map(str, args))
        self.calls.append((args, kwargs))
        if args[:3] == (sys.executable, "-I", "-c") and args[3] == PROGRAM:
            target = Path(args[5])
            assert target.is_relative_to(self.root)
            completed = subprocess.run(args, capture_output=True, text=True, check=False)
            if completed.returncode:
                raise CommandError(completed.stderr)
            return Output(0, completed.stdout)
        value = self.outputs.get(args)
        if value is None:
            raise AssertionError(f"unexpected command: {args}")
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value()
        rc, text = value
        if rc not in kwargs.get("allowed", (0,)):
            raise CommandError(f"{args[0]} failed")
        return Output(rc, text)


def test_config_conflict_preserved_and_identical_write_is_noop(tmp_path):
    ctx = Fixture(tmp_path)
    path = ctx.path("/etc/example.conf")
    Change(ctx, path, "managed\n").commit()
    before = path.stat().st_mtime_ns
    ctx.outputs[("cat", str(path))] = (0, "managed\n")
    Change(ctx, path, "managed\n").commit()
    assert path.stat().st_mtime_ns == before
    with pytest.raises(ManualConfig):
        Change(ctx, path, "different\n")
    assert path.read_text() == "managed\n"


@pytest.mark.parametrize("existing", [False, True])
def test_config_rollback_restores_previous_bytes_and_mode(tmp_path, existing):
    ctx = Fixture(tmp_path)
    path = ctx.path("/etc/example.conf")
    if existing:
        path.parent.mkdir(parents=True)
        path.write_text("user settings\n")
        path.chmod(0o640)
    change = Change(ctx, path, "new settings\n", replace=True)
    assert path.read_text() == "new settings\n"
    change.rollback()
    if existing:
        assert path.read_text() == "user settings\n"
        assert path.stat().st_mode & 0o777 == 0o640
    else:
        assert not path.exists()


@pytest.mark.parametrize("kind", ["symlink", "dangling", "directory"])
def test_config_nonregular_paths_are_never_replaced(tmp_path, kind):
    ctx = Fixture(tmp_path)
    target = ctx.path("/etc/example.conf")
    target.parent.mkdir(parents=True)
    original = tmp_path / "original"
    original.write_text("preserve")
    if kind == "directory":
        target.mkdir()
    else:
        target.symlink_to(original if kind == "symlink" else tmp_path / "absent")
    with pytest.raises((CommandError, ManualConfig, AssertionError)):
        Change(ctx, target, "overwrite", replace=True)
    assert original.read_text() == "preserve"
    assert target.is_dir() if kind == "directory" else target.is_symlink()


def test_profile_append_preserves_unterminated_line_and_deduplicates(tmp_path):
    path = tmp_path / ".profile"
    path.write_text("user settings")
    append_once(path, "export X=yes")
    append_once(path, "export X=yes")
    assert path.read_text() == "user settings\nexport X=yes\n"


def test_ssh_policy_accepts_effective_settings_not_file_format(tmp_path, monkeypatch):
    ctx = Fixture(
        tmp_path,
        outputs={
            ("/usr/sbin/sshd", "-T"): (
                0,
                "port 2222\npermitrootlogin no\npubkeyauthentication yes\npasswordauthentication no\n",
            )
        },
    )
    monkeypatch.setattr(ssh, "available", lambda *_: True)
    assert ssh.probe(ctx, Task("ssh-harden", "")).outcome == "satisfied"


def test_ssh_custom_config_is_manual_and_unchanged(tmp_path, monkeypatch):
    ctx = Fixture(tmp_path, outputs={("/usr/sbin/sshd", "-T"): (0, "passwordauthentication yes\n")})
    config = ctx.path("/etc/ssh/sshd_config.d/99-local.conf")
    config.parent.mkdir(parents=True)
    config.write_text("# keep\nPort 2222\n")
    monkeypatch.setattr(ssh, "available", lambda *_: True)
    result = ssh.probe(ctx, Task("ssh-harden", ""))
    assert result.outcome == "manual" and "skipped" in result.reason
    assert config.read_text() == "# keep\nPort 2222\n"


def test_ssh_failed_validation_removes_only_new_file(tmp_path, monkeypatch):
    ctx = Fixture(
        tmp_path,
        outputs={
            ("/usr/sbin/sshd", "-t"): CommandError("bad config"),
            ("systemctl", "reload", "ssh"): (0, ""),
        },
        phase="apply",
    )
    monkeypatch.setattr(ssh, "valid_operator", lambda _: True)
    result = ssh.apply(ctx, Task("ssh-harden", ""))
    assert result.outcome == "failed" and "restored" in result.reason
    assert not ctx.path("/etc/ssh/sshd_config.d/99-local.conf").exists()


def test_proxmox_requires_proven_operator_login(tmp_path, monkeypatch):
    ctx = Fixture(tmp_path, profile="proxmox", phase="apply")
    monkeypatch.setattr(ssh, "valid_operator", lambda _: True)
    monkeypatch.setattr(ssh, "operator_login_seen", lambda _: False)
    assert ssh.apply(ctx, Task("ssh-harden", "")).outcome == "deferred"
    assert ctx.calls == []


def test_proxmox_root_policy_is_key_only_not_disabled(tmp_path):
    ctx = Fixture(tmp_path, profile="proxmox")
    assert ssh.desired(ctx)["permitrootlogin"] == "prohibit-password"
    assert ssh.desired(ctx)["kbdinteractiveauthentication"] == "no"


def test_desktop_power_recovery_is_manual_without_mutation(tmp_path):
    ctx = Fixture(tmp_path)
    result = power.probe(ctx, Task("power-restore", ""))
    assert result.outcome == "manual" and "BIOS/UEFI" in result.action
    assert ctx.calls == []


def test_vm_power_recovery_is_not_applicable(tmp_path):
    ctx = Fixture(
        tmp_path,
        outputs={
            ("have", "systemd-detect-virt"): True,
            ("systemd-detect-virt", "--quiet"): (0, ""),
        },
    )
    assert power.probe(ctx, Task("power-restore", "")).outcome == "not-applicable"


def test_mac_power_sets_only_autorestart(tmp_path):
    ctx = Fixture(
        tmp_path,
        profile="macos",
        outputs={
            ("pmset", "-g", "cap"): (0, "autorestart\nsleep\n"),
            ("pmset", "-a", "autorestart", "1"): (0, ""),
        },
        phase="apply",
    )
    assert power.apply(ctx, Task("power-restore", "")).outcome == "changed"
    assert [a for a, _ in ctx.calls] == [
        ("pmset", "-g", "cap"),
        ("pmset", "-a", "autorestart", "1"),
    ]


def test_nvm_incomplete_directory_is_preserved(tmp_path):
    ctx = Fixture(tmp_path, phase="apply")
    nvm = ctx.home / ".nvm"
    nvm.mkdir()
    (nvm / "keep").write_text("operator data")
    assert node.apply(ctx, Task("node", "")).outcome == "manual"
    assert (nvm / "keep").read_text() == "operator data" and ctx.calls == []


def test_firstmate_worktree_and_conflicting_path_are_preserved(tmp_path):
    ctx = Fixture(tmp_path)
    target = ctx.home / "tools/firstmate"
    target.mkdir(parents=True)
    assert repository.probe(ctx, Task("firstmate", "")).outcome == "manual"
    (target / ".git").write_text("gitdir: somewhere")
    assert repository.probe(ctx, Task("firstmate", "")).outcome == "satisfied"
    assert ctx.calls == []


def test_tailscale_login_is_deferred_without_tty(tmp_path):
    ctx = Fixture(tmp_path, phase="apply")
    assert tailscale.apply(ctx, Task("tailscale.login", "")).outcome == "deferred"
    assert ctx.calls == []


def test_shareable_diagnostics_redact_urls_and_tokens():
    text = redact("open https://example.invalid/login?token=abc password=secret token:xyz")
    assert "example.invalid" not in text and "secret" not in text and "abc" not in text


def test_interrupted_write_is_reconciled_on_retry(tmp_path):
    ctx = Fixture(tmp_path)
    path = ctx.path("/etc/interrupted.conf")
    path.parent.mkdir(parents=True)
    path.write_text("original\n")
    # Simulate a worker dying after publication, before commit/reload.
    Change(ctx, path, "candidate\n", replace=True)
    assert path.read_text() == "candidate\n"
    retry = Change(ctx, path, "candidate\n", replace=True)
    retry.rollback()
    assert path.read_text() == "original\n"


def test_interrupted_write_never_overwrites_operator_edit(tmp_path):
    ctx = Fixture(tmp_path)
    path = ctx.path("/etc/interrupted.conf")
    Change(ctx, path, "candidate\n")
    path.write_text("operator edit after interruption\n")
    with pytest.raises(CommandError):
        Change(ctx, path, "candidate\n")
    assert path.read_text() == "operator edit after interruption\n"


def test_failed_download_does_not_execute_partial_installer(tmp_path):
    from setup_tasks.common import installer

    ctx = Fixture(tmp_path, phase="apply")
    calls = []

    def fail_download(*args, **kwargs):
        calls.append(args)
        Path(args[-1]).write_text("dangerous partial installer")
        raise CommandError("download failed")

    ctx.run = fail_download
    with pytest.raises(CommandError):
        installer(ctx, "https://example.invalid/install.sh")
    assert len(calls) == 1 and calls[0][0] == "curl"


@pytest.mark.parametrize("fails", [False, True])
def test_subnet_routing_preserves_settings_and_rolls_back(tmp_path, fails):
    from setup_tasks import routing

    ctx = Fixture(tmp_path, profile="proxmox", phase="apply")
    config = ctx.path("/etc/sysctl.d/99-tailscale.conf")
    config.parent.mkdir(parents=True)
    original = "# operator comment\nnet.ipv6.conf.all.forwarding = 0\nnet.ipv4.ip_forward = 0\n"
    config.write_text(original)
    ctx.inputs["lan_route"] = "192.168.2.0/24"
    command = ("tailscale", "set", "--advertise-routes=10.0.0.0/8,192.168.2.0/24")
    ctx.outputs.update(
        {
            ("tailscale", "set", "--help"): (0, ""),
            ("tailscale", "debug", "prefs"): (
                0,
                '{"AdvertiseRoutes":["10.0.0.0/8","0.0.0.0/0","::/0"]}',
            ),
            ("cat", str(config)): (0, original),
            ("sysctl", "-n", "net.ipv4.ip_forward"): (0, "0"),
            ("sysctl", "-w", "net.ipv4.ip_forward=1"): (0, ""),
            ("sysctl", "-w", "net.ipv4.ip_forward=0"): (0, ""),
            command: CommandError("injected failure") if fails else (0, ""),
        }
    )
    if fails:
        with pytest.raises(CommandError):
            routing.apply(ctx, Task("subnet-router", ""))
        assert config.read_text() == original
        assert ("sysctl", "-w", "net.ipv4.ip_forward=0") in [args for args, _ in ctx.calls]
    else:
        assert routing.apply(ctx, Task("subnet-router", "")).outcome == "changed"
        assert config.read_text() == original.replace(
            "net.ipv4.ip_forward = 0", "net.ipv4.ip_forward = 1"
        )
    assert command in [args for args, _ in ctx.calls]


def test_proxmox_keys_preserve_cluster_symlink_and_deduplicate_material(tmp_path):
    from setup_core.context import environment
    from setup_tasks import ssh_keys

    home = tmp_path / "home"
    home.mkdir()
    ctx = Context("proxmox", home, "root", environment(home, {}), phase="apply", task_id="ssh-keys")
    keys = []
    for name in ("cluster", "operator"):
        path = tmp_path / name
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
        keys.append(path.with_suffix(".pub").read_text().strip())
    auth, _ = ssh_keys.paths(ctx)
    auth.parent.mkdir()
    cluster = tmp_path / "cluster-authorized"
    cluster.write_text(keys[0] + "\n")
    auth.symlink_to(cluster)
    ctx.inputs["ssh_key"] = keys[1]
    assert not ssh_keys.valid_operator(ctx)
    assert ssh_keys.apply(ctx, Task("ssh-keys", "")).outcome == "changed"
    ctx.inputs["ssh_key"] = " ".join(keys[1].split()[:2]) + " another comment"
    ssh_keys.apply(ctx, Task("ssh-keys", ""))
    assert auth.is_symlink()
    assert cluster.read_text().splitlines().count(keys[0]) == 1
    assert cluster.read_text().count(keys[1].split()[1]) == 1
    assert ssh_keys.valid_operator(ctx)


def test_node_shell_adapter_preserves_default_and_uses_custom_directory(tmp_path):
    nvm = tmp_path / "custom nvm"
    (nvm / "alias").mkdir(parents=True)
    (nvm / "alias/default").write_text("v20.0.0\n")
    (nvm / "nvm.sh").write_text('nvm() { printf "%s\\n" "$*" >> "$NVM_DIR/calls"; }\n')
    adapter = Path(__file__).resolve().parents[1] / "setup_adapters/node.sh"
    subprocess.run(
        ["bash", str(adapter)], env={"PATH": "/usr/bin:/bin", "NVM_DIR": str(nvm)}, check=True
    )
    assert (nvm / "alias/default").read_text() == "v20.0.0\n"
    assert (nvm / "calls").read_text() == "install 22\n"


def test_herdr_integration_verifies_current_and_preserves_custom_settings(tmp_path):
    from setup_tasks import apps

    ctx = Fixture(
        tmp_path,
        outputs={
            ("have", "herdr"): True,
            ("have", "claude"): True,
            ("herdr", "integration", "status"): (0, "claude: current (v10)\n"),
        },
    )
    task = Task("herdr.integration", "")
    assert apps.probe(ctx, task).outcome == "satisfied"
    settings = ctx.home / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"hooks": {"custom": true}}')
    ctx.outputs[("herdr", "integration", "status")] = (0, "claude: outdated (v9 < v10)\n")
    assert apps.apply(ctx, task).outcome == "manual"
    assert settings.read_text() == '{"hooks": {"custom": true}}'
    assert all("install" not in args for args, _ in ctx.calls)
