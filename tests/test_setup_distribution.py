"""Exercise the built artifact and public entrypoints outside a checkout."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from setup_core.context import Context, environment
from setup_core.distribution import build
from setup_core.model import Task
from setup_tasks import launcher

ROOT = Path(__file__).resolve().parents[1]


def test_bundle_is_deterministic_and_checksums_match(tmp_path):
    a = build(ROOT, tmp_path / "a").read_bytes()
    b = build(ROOT, tmp_path / "b").read_bytes()
    assert a == b
    assert (tmp_path / "a/setup.pyz.sha256").read_text().split()[0] == hashlib.sha256(a).hexdigest()


def test_bundle_plans_from_unrelated_working_directory(tmp_path):
    bundle = build(ROOT, tmp_path / "bundle")
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(bundle),
            "--plan",
            "--profile",
            "debian",
            "--only",
            "pi",
            "--with-deps",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    names = [line.split()[0] for line in result.stdout.splitlines()]
    assert names.index("node") < names.index("pi")
    assert "download-tools" in names and "ssh" not in names


def test_bundle_worker_applies_and_probes_fixture_task(tmp_path):
    bundle = build(ROOT, tmp_path / "bundle")
    home = tmp_path / "home"
    home.mkdir()
    request, response = tmp_path / "request", tmp_path / "response"
    data = {
        "profile": "debian",
        "home": str(home),
        "user": "fixture",
        "env": environment(home, {}),
        "inputs": {},
        "task": "directories",
        "phase": "apply",
    }
    request.write_text(json.dumps(data))
    result = subprocess.run(
        [sys.executable, "-I", str(bundle), "--worker", str(request), str(response)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(response.read_text())["outcome"] == "changed"
    data["phase"] = "probe"
    request.write_text(json.dumps(data))
    subprocess.run(
        [sys.executable, "-I", str(bundle), "--worker", str(request), str(response)], check=True
    )
    assert json.loads(response.read_text())["outcome"] == "satisfied"
    assert (home / "tools").is_dir() and (home / "projects").is_dir()


def test_launcher_preserves_custom_file_and_installs_runnable_bundle(tmp_path):
    ctx = Context(
        "debian",
        tmp_path,
        "fixture",
        environment(tmp_path, {}),
        phase="apply",
        task_id="setup-command",
    )
    result = launcher.apply(ctx, Task("setup-command", ""))
    assert result.outcome == "changed"
    command = tmp_path / ".local/bin/setup-machine"
    process = subprocess.run(
        [str(command), "--plan", "--profile", "debian", "--only", "directories"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    assert "directories" in process.stdout
    assert launcher.probe(ctx, Task("setup-command", "")).outcome == "satisfied"
    command.write_text("my custom launcher\n")
    assert launcher.apply(ctx, Task("setup-command", "")).outcome == "satisfied"
    assert command.read_text() == "my custom launcher\n"
