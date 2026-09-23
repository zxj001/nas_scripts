"""Task/file contracts use controlled command responses and fixture-only paths."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from setup_core.commands import CommandError, Output, redact
from setup_core.context import Context
from setup_core.files import PROGRAM, Change, ManualConfig, append_once
from setup_core.model import Task
from setup_tasks import node, packages, power, repos, repository, shellfish, ssh, tailscale


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
        # (rc, output) or (rc, stdout, stderr); stderr stays apart only with split.
        rc, text, errors = (*value, "") if len(value) == 2 else value
        if rc not in kwargs.get("allowed", (0,)):
            raise CommandError(f"{args[0]} failed")
        if kwargs.get("split"):
            return Output(rc, text, errors)
        return Output(rc, errors + text)


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


@pytest.mark.parametrize("custom", [False, True])
def test_firstmate_worktree_and_conflicting_path_are_preserved(tmp_path, custom):
    ctx = Fixture(tmp_path)
    target = ctx.home / "firstmate"
    if custom:
        target = tmp_path / "custom tools/firstmate"
        ctx.inputs["firstmate_dir"] = str(target)
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


def test_firstmate_clones_into_custom_directory(tmp_path):
    target = tmp_path / "custom tools/firstmate"
    ctx = Fixture(
        tmp_path,
        phase="apply",
        outputs={("git", "clone", "https://github.com/kunchenguid/firstmate", str(target)): (0, "")},
    )
    ctx.inputs["firstmate_dir"] = str(target)
    assert repository.apply(ctx, Task("firstmate", "")).outcome == "changed"
    assert target.parent.is_dir()


@pytest.mark.parametrize("missing", [None, "pip", "venv"])
def test_python_needs_pip_and_venv_on_debian(tmp_path, missing):
    ctx = Fixture(
        tmp_path,
        outputs={
            ("have", "python3"): True,
            ("python3", "-m", "pip", "--version"): (1 if missing == "pip" else 0, ""),
            ("python3", "-c", "import ensurepip, venv"): (1 if missing == "venv" else 0, ""),
        },
    )
    outcome = packages.probe(ctx, Task("python", "")).outcome
    assert outcome == ("satisfied" if missing is None else "pending")


def test_repos_task_maps_the_adapter_exit_code(tmp_path, monkeypatch):
    codes = {"probe": 1}
    monkeypatch.setattr(repos, "adapter", lambda ctx, op, allowed=(0,): Output(codes[op], ""))
    ctx = Fixture(tmp_path, profile="proxmox")
    assert repos.probe(ctx, Task("repos", "")).outcome == "pending"
    codes["probe"] = 0
    assert repos.probe(ctx, Task("repos", "")).outcome == "satisfied"


def shellfish_fixture(
    tmp_path,
    *,
    tools=True,
    crontab=(1, "", "no crontab for fixture"),
    profile="debian",
    target="",
):
    outputs = {("have", t): tools for t in shellfish.TOOLS}
    outputs[("crontab", "-l")] = crontab
    ctx = Fixture(tmp_path, profile=profile, phase="apply", outputs=outputs)
    if target is not None:
        ctx.inputs["widget_target"] = target
    written = []

    def install_crontab(args):
        written.append(Path(args[1]).read_text())
        ctx.outputs[("crontab", "-l")] = (0, written[-1])
        return Output(0, "")

    base = ctx.run

    def run(*args, **kwargs):
        args = tuple(map(str, args))
        if args[0] == "crontab" and args[1] != "-l":
            ctx.calls.append((args, kwargs))
            return install_crontab(args)
        if args == (str(shellfish.target(ctx)),):
            ctx.calls.append((args, kwargs))
            return Output(0, "")
        return base(*args, **kwargs)

    ctx.run = run
    return ctx, written


def test_shellfish_is_manual_without_shell_integration(tmp_path):
    ctx, written = shellfish_fixture(tmp_path)
    result = shellfish.probe(ctx, Task("shellfish", ""))
    assert result.outcome == "manual" and "Install Shell Integration" in result.action
    assert shellfish.apply(ctx, Task("shellfish", "")).outcome == "manual"
    assert written == [] and ctx.calls == []


def test_shellfish_not_applicable_on_macos(tmp_path):
    ctx, _ = shellfish_fixture(tmp_path, profile="macos")
    assert shellfish.probe(ctx, Task("shellfish", "")).outcome == "not-applicable"


def test_shellfish_installs_keeps_crontab_migrates_old_line_and_repeats(tmp_path):
    old = "*/15 * * * * /home/fixture/tools/nas_scripts/scripts/shellfish_widget.sh >/dev/null 2>&1"
    ctx, written = shellfish_fixture(tmp_path, crontab=(0, "0 3 * * * backup\n" + old + "\n"))
    (ctx.home / ".shellfishrc").write_text("")
    task = Task("shellfish", "")
    assert shellfish.probe(ctx, task).outcome == "pending"
    assert shellfish.apply(ctx, task).outcome == "changed"
    path = shellfish.target(ctx)
    assert path.read_text() == shellfish.bundled() and os.access(path, os.X_OK)
    assert written[-1].splitlines() == ["0 3 * * * backup", shellfish.cron_line(path)]
    assert shellfish.probe(ctx, task).outcome == "satisfied"
    assert shellfish.apply(ctx, task).outcome == "satisfied"
    assert len(written) == 1
    assert sum(1 for args, _ in ctx.calls if args == (str(path),)) == 1


def test_shellfish_keeps_arguments_of_a_customized_old_line(tmp_path):
    old = "*/15 * * * * /home/u/nas_scripts/scripts/shellfish_widget.sh / /media/Drive1 >/dev/null 2>&1"
    ctx, written = shellfish_fixture(tmp_path, crontab=(0, old + "\n"))
    (ctx.home / ".shellfishrc").write_text("")
    assert shellfish.apply(ctx, Task("shellfish", "")).outcome == "changed"
    path = shellfish.target(ctx)
    assert written[-1].splitlines() == [
        f"*/15 * * * * {path} / /media/Drive1 >/dev/null 2>&1"
    ]


def test_shellfish_crontab_stderr_is_not_written_back(tmp_path):
    ctx, written = shellfish_fixture(
        tmp_path, crontab=(0, "0 3 * * * backup\n", "crontab: some warning\n")
    )
    (ctx.home / ".shellfishrc").write_text("")
    assert shellfish.apply(ctx, Task("shellfish", "")).outcome == "changed"
    assert "warning" not in written[-1]
    read = [kwargs for args, kwargs in ctx.calls if args == ("crontab", "-l")]
    assert read and all(k.get("quiet") and k.get("split") for k in read)


def test_shellfish_proxmox_installs_to_usr_local_bin(tmp_path):
    ctx, written = shellfish_fixture(tmp_path, profile="proxmox")
    (ctx.home / ".shellfishrc").write_text("")
    assert shellfish.apply(ctx, Task("shellfish", "")).outcome == "changed"
    assert shellfish.target(ctx) == tmp_path / "usr/local/bin/shellfish_widget.sh"
    assert shellfish.target(ctx).is_file()


def test_shellfish_installs_missing_tools_with_apt(tmp_path):
    ctx, _ = shellfish_fixture(tmp_path, tools=False)
    (ctx.home / ".shellfishrc").write_text("")
    install = ("apt-get", "-o", "Dpkg::Options::=--force-confold", "install", "--no-remove", "-y",
               "openssl", "xxd", "curl", "cron")
    ctx.outputs[("apt-get", "update")] = (0, "")
    ctx.outputs[install] = (0, "")
    shellfish.apply(ctx, Task("shellfish", ""))
    assert any(args == install for args, _ in ctx.calls)


def test_shellfish_unreadable_crontab_is_never_overwritten(tmp_path):
    ctx, written = shellfish_fixture(tmp_path, crontab=(1, "", "crontab: permission denied"))
    (ctx.home / ".shellfishrc").write_text("")
    with pytest.raises(RuntimeError, match="cannot read crontab"):
        shellfish.apply(ctx, Task("shellfish", ""))
    assert written == []


def test_shellfish_operator_script_at_target_is_preserved(tmp_path):
    ctx, written = shellfish_fixture(tmp_path)
    (ctx.home / ".shellfishrc").write_text("")
    path = shellfish.target(ctx)
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/sh\necho mine\n")
    assert shellfish.apply(ctx, Task("shellfish", "")).outcome == "manual"
    assert path.read_text() == "#!/bin/sh\necho mine\n" and written == []


@pytest.mark.parametrize("script_dir", ["setup_adapters", "scripts"])
def test_shellfish_widget_tolerates_shellfishrc_unset_variables(tmp_path, script_dir):
    # The real file reads $TMUX and friends, which cron leaves unset.
    if not Path("/proc/stat").exists():
        pytest.skip("reads /proc")
    (tmp_path / ".shellfishrc").write_text(
        'if [[ -n "$TMUX" ]]; then :; fi\nfalse\nwidget() { echo "$*" >"$HOME/sent"; }\n'
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("openssl", "xxd", "curl"):
        (bin_dir / tool).write_text("#!/bin/sh\n")
        (bin_dir / tool).chmod(0o755)
    script = Path(__file__).resolve().parents[1] / script_dir / "shellfish_widget.sh"
    result = subprocess.run(
        [str(script), "--name", "NAS"],
        text=True,
        capture_output=True,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "sent").read_text().startswith("server.rack --text NAS ")


def test_shellfish_widget_print_layout_and_colors(tmp_path):
    if not Path("/proc/stat").exists():
        pytest.skip("reads /proc")
    script = Path(__file__).resolve().parents[1] / "setup_adapters/shellfish_widget.sh"
    result = subprocess.run(
        [str(script), "--print", "--target", "pve1", "--name", "NAS", "/", str(tmp_path)],
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    color = r"#[0-9a-f]{6}"
    pattern = (
        rf"--target pve1 server\.rack --text NAS cpu\.fill {color} \d+% foreground CPU "
        rf"(thermometer\.medium {color} \d+°C foreground Temp )?"
        rf"memorychip {color} \d+% foreground Mem "
        rf"internaldrive {color} \d+% foreground Disk "
        rf"internaldrive {color} \d+% foreground {re.escape(tmp_path.name)}"
    )
    assert re.fullmatch(pattern, result.stdout.strip()), result.stdout
    definitions = script.read_text().removesuffix('main "$@"\n')
    levels = subprocess.run(
        ["bash", "-c", definitions + """
[ "$(level_color 74% 75 90)" = "$GREEN" ]
[ "$(level_color 75% 75 90)" = "$ORANGE" ]
[ "$(level_color 90% 75 90)" = "$RED" ]
[ "$(level_color 71°C 70 85)" = "$ORANGE" ]
"""],
        text=True,
        capture_output=True,
    )
    assert levels.returncode == 0, levels.stderr


def test_execute_split_keeps_stderr_out_of_stdout():
    from setup_core.commands import execute

    script = "import sys; print('data'); print('warning', file=sys.stderr)"
    env = {"PATH": os.environ["PATH"]}
    split = execute([sys.executable, "-c", script], env=env, split=True)
    assert split.stdout == "data\n" and split.stderr == "warning\n"
    merged = execute([sys.executable, "-c", script], env=env, timeout=30)
    assert "warning" in merged.stdout


def test_append_once_keeps_an_existing_files_mode(tmp_path):
    profile = tmp_path / ".bashrc"
    profile.write_text("# mine\n")
    profile.chmod(0o644)
    append_once(profile, "export X=1")
    assert profile.stat().st_mode & 0o777 == 0o644
    created = tmp_path / "new"
    append_once(created, "x")
    assert created.stat().st_mode & 0o777 == 0o600


def test_repos_active_subscription_counts_as_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(repos, "adapter", lambda ctx, op, allowed=(0,): Output(1, ""))
    ctx = Fixture(
        tmp_path,
        profile="proxmox",
        outputs={
            ("have", "pvesubscription"): True,
            ("pvesubscription", "get"): (0, "key: pve1c-x\nstatus: active\n"),
        },
    )
    assert repos.probe(ctx, Task("repos", "")).outcome == "satisfied"
    assert repos.usable(ctx)
    ctx.outputs[("pvesubscription", "get")] = (0, "status: notfound\n")
    assert repos.probe(ctx, Task("repos", "")).outcome == "pending"
    assert not repos.usable(ctx)


def test_gpu_driver_not_loaded_is_pending_not_a_failure(tmp_path):
    from setup_tasks import gpu

    ctx = Fixture(
        tmp_path,
        outputs={
            ("have", "lspci"): True,
            ("have", "nvidia-smi"): True,
            ("lspci", "-nn"): (0, "01:00.0 VGA compatible controller [0300]: NVIDIA Corp\n"),
            ("nvidia-smi",): (9, "NVIDIA-SMI has failed because it couldn't communicate"),
        },
    )
    assert gpu.probe(ctx, Task("gpu", "")).outcome == "pending"
    ctx.outputs[("nvidia-smi",)] = (0, "")
    assert gpu.probe(ctx, Task("gpu", "")).outcome == "satisfied"


def test_gpu_without_lspci_is_not_applicable(tmp_path):
    from setup_tasks import gpu

    assert gpu.probe(Fixture(tmp_path), Task("gpu", "")).outcome == "not-applicable"


@pytest.mark.parametrize("kind", ["private", "two-lines"])
def test_operator_key_must_be_one_public_key(tmp_path, kind):
    from setup_tasks import ssh_keys

    key = tmp_path / "id"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    ctx = Fixture(tmp_path, phase="apply")
    public = key.with_suffix(".pub").read_text().strip()
    ctx.inputs["ssh_key"] = key.read_text() if kind == "private" else public + "\nextra junk"
    ctx.run = lambda *a, **k: pytest.fail("the key reached ssh-keygen")
    assert ssh_keys.apply(ctx, Task("ssh-keys", "")).outcome == "failed"
    assert not (ctx.home / ".ssh/authorized_keys").exists()


def test_operator_login_seen_needs_the_operators_key_in_the_journal(tmp_path):
    from setup_tasks import ssh_keys

    auth, recorded = ssh_keys.paths(ctx := Fixture(tmp_path, profile="proxmox", phase="apply"))
    auth.parent.mkdir()
    auth.write_text("keys\n")
    recorded.write_text("SHA256:operator\n")
    listing = "256 SHA256:operator op (ED25519)\n256 SHA256:node root@pve1 (ED25519)\n"
    ctx.outputs[("ssh-keygen", "-l", "-f", str(auth))] = (0, listing)
    grep = ("journalctl", "-u", "ssh.service", "--no-pager", "-o", "cat",
            "--grep", "Accepted publickey for root ")
    ctx.outputs[grep] = (0, "Accepted publickey for root from 10.0.0.2 port 1 ssh2: ED25519 SHA256:node\n")
    assert not ssh_keys.operator_login_seen(ctx)
    ctx.outputs[grep] = (1, "", "-- No entries --")
    assert not ssh_keys.operator_login_seen(ctx)
    ctx.outputs[grep] = (0, "Accepted publickey for root from 10.0.0.3 port 2 ssh2: ED25519 SHA256:operator\n")
    assert ssh_keys.operator_login_seen(ctx)
    kwargs = [k for args, k in ctx.calls if args == grep][-1]
    assert kwargs["quiet"] and kwargs["split"] and kwargs["timeout"]


def test_ssh_harden_refuses_without_an_operator_key(tmp_path):
    ctx = Fixture(tmp_path, phase="apply")
    result = ssh.apply(ctx, Task("ssh-harden", ""))
    assert result.outcome == "blocked"
    assert not ctx.path("/etc/ssh/sshd_config.d/99-local.conf").exists()
    assert ctx.calls == []


@pytest.mark.parametrize("failure", ["overridden", "reload"])
def test_ssh_rolls_back_when_overridden_or_reload_fails(tmp_path, monkeypatch, failure):
    reloads = []

    def reload():
        reloads.append(1)
        if failure == "reload" and len(reloads) == 1:
            raise CommandError("reload failed")
        return Output(0, "")

    ctx = Fixture(
        tmp_path,
        outputs={("/usr/sbin/sshd", "-t"): (0, ""), ("systemctl", "reload", "ssh"): reload},
        phase="apply",
    )
    monkeypatch.setattr(ssh, "valid_operator", lambda _: True)
    monkeypatch.setattr(ssh, "policy_ok", lambda _: failure != "overridden")
    result = ssh.apply(ctx, Task("ssh-harden", ""))
    assert result.outcome == "failed" and "restored" in result.reason
    assert not ctx.path("/etc/ssh/sshd_config.d/99-local.conf").exists()
    assert reloads  # sshd reloaded with the restored configuration


def test_mac_tailscale_uses_the_app_cli_without_sudo(tmp_path):
    ctx = Fixture(tmp_path, profile="macos", phase="apply")
    app = ctx.path(tailscale.MAC_APP_CLI)
    app.parent.mkdir(parents=True)
    app.write_text("")
    ctx.outputs[(str(app), "status")] = (0, "100.1.2.3 mac")
    task = Task("tailscale.login", "")
    assert tailscale.probe(ctx, task).outcome == "satisfied"
    ctx.outputs[(str(app), "status")] = (1, "Logged out.")
    assert tailscale.probe(ctx, task).outcome == "manual"
    assert tailscale.apply(ctx, task).outcome == "manual"
    assert not any("up" in args for args, _ in ctx.calls)


def test_shellfish_sends_to_this_machines_widget(tmp_path, monkeypatch):
    monkeypatch.setattr(shellfish.socket, "gethostname", lambda: "pve1.home.arpa")
    ctx, written = shellfish_fixture(
        tmp_path, profile="proxmox", target=None, crontab=(0, "0 3 * * * vzdump\n")
    )
    (ctx.home / ".shellfishrc").write_text("")
    task = Task("shellfish", "")
    assert shellfish.apply(ctx, task).outcome == "changed"
    path = shellfish.target(ctx)
    assert written[-1].splitlines() == [
        "0 3 * * * vzdump",
        f"*/15 * * * * {path} --target pve1 >/dev/null 2>&1",
    ]
    assert shellfish.probe(ctx, task).outcome == "satisfied"


def test_shellfish_adds_a_target_to_an_existing_line_and_keeps_its_arguments(tmp_path):
    old = "*/15 * * * * /home/u/nas_scripts/scripts/shellfish_widget.sh / /media/Drive1 >/dev/null 2>&1"
    ctx, written = shellfish_fixture(tmp_path, target="debian-mini", crontab=(0, old + "\n"))
    (ctx.home / ".shellfishrc").write_text("")
    task = Task("shellfish", "")
    path = shellfish.target(ctx)
    shellfish.install(path, shellfish.bundled())
    assert shellfish.probe(ctx, task).outcome == "pending"
    assert shellfish.apply(ctx, task).outcome == "changed"
    assert written[-1].splitlines() == [
        f"*/15 * * * * {path} --target debian-mini / /media/Drive1 >/dev/null 2>&1"
    ]
    assert shellfish.probe(ctx, task).outcome == "satisfied"


def test_shellfish_keeps_a_target_the_operator_chose(tmp_path):
    ctx, written = shellfish_fixture(tmp_path, target="debian-mini")
    (ctx.home / ".shellfishrc").write_text("")
    path = shellfish.target(ctx)
    shellfish.install(path, shellfish.bundled())
    line = f"*/15 * * * * {path} --target phone2 >/dev/null 2>&1"
    ctx.outputs[("crontab", "-l")] = (0, line + "\n")
    assert shellfish.probe(ctx, Task("shellfish", "")).outcome == "satisfied"
    assert written == []


def test_empty_widget_target_means_the_shared_widget(tmp_path):
    ctx, written = shellfish_fixture(tmp_path, target="")
    (ctx.home / ".shellfishrc").write_text("")
    shellfish.apply(ctx, Task("shellfish", ""))
    assert "--target" not in written[-1]


APT_INSTALL = ("apt-get", "-o", "Dpkg::Options::=--force-confold", "install", "--no-remove", "-y")


def docker_fixture(tmp_path, monkeypatch, outputs, *, members=(), login=False):
    from types import SimpleNamespace

    from setup_tasks import docker

    group = SimpleNamespace(gr_gid=990, gr_mem=list(members))
    monkeypatch.setattr(docker, "group", lambda: group)
    monkeypatch.setattr(docker.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(docker.os, "getgroups", lambda: [1000, *([990] if login else [])])
    ctx = Fixture(tmp_path, phase="apply", outputs=outputs)
    ctx.outputs.update(
        {
            ("apt-get", "update"): (0, ""),
            ("systemctl", "enable", "--now", "docker"): (0, ""),
            ("/usr/sbin/usermod", "-aG", "docker", "fixture"): lambda: (
                group.gr_mem.append("fixture") or Output(0, "")
            ),
        }
    )
    return docker, group, ctx


def installed_docker(overrides=None):
    return {
        ("have", "dockerd"): True,
        ("have", "docker"): True,
        ("docker", "compose", "version"): (0, "Docker Compose version 2.26.1"),
        ("systemctl", "is-enabled", "--quiet", "docker"): (0, ""),
        ("systemctl", "is-active", "--quiet", "docker"): (0, ""),
        **(overrides or {}),
    }


def test_docker_installs_engine_compose_and_group_then_needs_a_new_login(tmp_path, monkeypatch):
    docker, group, ctx = docker_fixture(
        tmp_path,
        monkeypatch,
        {
            ("docker", "compose", "version"): (1, ""),
            ("dpkg-query", "-W", "-f=${Status}", "docker-ce"): (1, ""),
            (*APT_INSTALL, "docker.io", "docker-cli", "docker-compose"): (0, ""),
        },
    )
    task = Task("docker", "")
    assert docker.probe(ctx, task).outcome == "pending"
    result = docker.apply(ctx, task)
    assert result.outcome == "deferred" and "Log out" in result.action
    assert group.gr_mem == ["fixture"]
    ctx.outputs.update(installed_docker())
    # Until the operator logs in again, status says so instead of claiming success.
    assert docker.probe(ctx, task).outcome == "deferred"


def test_docker_is_satisfied_only_when_usable_without_sudo(tmp_path, monkeypatch):
    docker, _, ctx = docker_fixture(
        tmp_path, monkeypatch, installed_docker(), members=("fixture",), login=True
    )
    task = Task("docker", "")
    assert docker.probe(ctx, task).outcome == "satisfied"
    ctx.outputs[("systemctl", "is-active", "--quiet", "docker")] = (3, "")
    assert docker.probe(ctx, task).outcome == "pending"
    ctx.outputs[("systemctl", "is-active", "--quiet", "docker")] = (0, "")
    ctx.outputs[("docker", "compose", "version")] = (125, "unknown command: docker compose")
    assert docker.probe(ctx, task).outcome == "pending"


def test_docker_adds_the_user_to_the_group_of_an_existing_install(tmp_path, monkeypatch):
    docker, group, ctx = docker_fixture(tmp_path, monkeypatch, installed_docker(), login=True)
    task = Task("docker", "")
    assert docker.probe(ctx, task).reason == "fixture is not in the docker group"
    assert docker.apply(ctx, task).outcome == "changed"
    assert group.gr_mem == ["fixture"]
    assert not any(args[0] == "apt-get" for args, _ in ctx.calls)


def test_docker_ce_without_compose_is_manual_and_installs_nothing(tmp_path, monkeypatch):
    docker, _, ctx = docker_fixture(
        tmp_path,
        monkeypatch,
        installed_docker(
            {
                ("docker", "compose", "version"): (1, ""),
                ("dpkg-query", "-W", "-f=${Status}", "docker-ce"): (0, "install ok installed"),
            }
        ),
    )
    result = docker.apply(ctx, Task("docker", ""))
    assert result.outcome == "manual" and "docker-compose-plugin" in result.action
    assert not any(args[0] in {"apt-get", "systemctl"} for args, _ in ctx.calls)


def test_mac_docker_desktop_is_manual_until_its_engine_runs(tmp_path, monkeypatch):
    from setup_tasks import docker

    ctx = Fixture(tmp_path, profile="macos", phase="apply")
    task = Task("docker", "")
    assert docker.probe(ctx, task).outcome == "pending"

    def install():
        cli = ctx.path(docker.MAC_APP_CLI)
        cli.parent.mkdir(parents=True)
        cli.write_text("")
        return Output(0, "")

    ctx.outputs[("brew", "install", "--cask", "docker-desktop")] = install
    cli = str(ctx.path(docker.MAC_APP_CLI))
    ctx.outputs[(cli, "compose", "version")] = (0, "Docker Compose version v2.39.1")
    ctx.outputs[(cli, "info")] = (1, "Cannot connect to the Docker daemon")
    result = docker.apply(ctx, task)
    assert result.outcome == "manual" and "Open Docker.app" in result.action
    ctx.outputs[(cli, "info")] = (0, "Server Version: 28.3.2")
    assert docker.probe(ctx, task).outcome == "satisfied"


@pytest.mark.parametrize("compose", [0, 1])
def test_mac_never_installs_docker_desktop_over_another_engine(tmp_path, compose):
    from setup_tasks import docker

    # OrbStack or Colima installed but stopped: the CLI is there, no engine runs.
    ctx = Fixture(
        tmp_path,
        profile="macos",
        phase="apply",
        outputs={
            ("have", "docker"): True,
            ("docker", "compose", "version"): (compose, ""),
            ("docker", "info"): (1, "Cannot connect to the Docker daemon"),
        },
    )
    result = docker.probe(ctx, Task("docker", ""))
    assert result.outcome == "manual"
    assert ("Start your Docker engine" in result.action) == (compose == 0)
    assert not any(args[0] == "brew" for args, _ in ctx.calls)


def test_chromium_installs_the_debian_package(tmp_path):
    from setup_tasks import browser

    ctx = Fixture(
        tmp_path,
        phase="apply",
        outputs={("apt-get", "update"): (0, ""), (*APT_INSTALL, "chromium"): (0, "")},
    )
    task = Task("chromium", "")
    assert browser.probe(ctx, task).outcome == "pending"
    assert browser.apply(ctx, task).outcome == "changed"
    ctx.outputs[("have", "chromium")] = True
    assert browser.probe(ctx, task).outcome == "satisfied"


@pytest.mark.parametrize("app", ["Chromium.app", "Google Chrome.app"])
def test_mac_accepts_an_installed_chromium_browser(tmp_path, app):
    from setup_tasks import browser

    ctx = Fixture(tmp_path, profile="macos")
    ctx.path("/Applications/" + app).mkdir(parents=True)
    assert browser.probe(ctx, Task("chromium", "")).outcome == "satisfied"


def test_mac_installs_google_chrome_when_no_chromium_browser(tmp_path):
    from setup_tasks import browser

    ctx = Fixture(
        tmp_path,
        profile="macos",
        phase="apply",
        outputs={("brew", "install", "--cask", "google-chrome"): (0, "")},
    )
    task = Task("chromium", "")
    assert browser.probe(ctx, task).outcome == "pending"
    assert browser.apply(ctx, task).outcome == "changed"
