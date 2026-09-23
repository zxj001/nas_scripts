"""Scheduler behavior, capability checks and isolated worker protocol."""

import json

import pytest

from setup_core.backend import Backend
from setup_core.context import environment
from setup_core.model import Result, Task, missing_providers, select, validate
from setup_core.runner import Runner
from setup_core.state import Journal, apply_lock


class FakeBackend:
    def __init__(self, capabilities=(), results=None, probes=None):
        self.capabilities = set(capabilities)
        self.results = results or {}
        self.probes = probes or {}
        self.calls = []
        self.completed = set()

    def call(self, task, phase):
        name = task if isinstance(task, str) else task.id
        self.calls.append((name, phase))
        if phase in ("capability", "health"):
            return Result(
                name, "satisfied" if name in self.capabilities else "pending", "unavailable"
            )
        if phase == "probe":
            return self.probes.get(
                name, Result(name, "satisfied" if name in self.completed else "pending")
            )
        result = self.results.get(name, Result(name, "changed"))
        if result.outcome == "changed":
            self.completed.add(name)
            self.capabilities.update(task.provides)
        return result


def run(tasks, backend, **kwargs):
    output = []
    runner = Runner(tasks, backend, yes=True, emit=output.append, **kwargs)
    code = runner.run()
    return code, {r.task: r for r in runner.results}, "\n".join(output)


def test_node_failure_blocks_pi_but_continues_independent_tasks():
    tasks = [Task("node", "", provides=("node",)), Task("pi", "", needs=("node",)), Task("ssh", "")]
    backend = FakeBackend(results={"node": Result("node", "failed", "download failed")})
    code, results, output = run(tasks, backend)
    assert code == 1
    assert [results[n].outcome for n in ("node", "pi", "ssh")] == ["failed", "blocked", "changed"]
    assert ("pi", "apply") not in backend.calls
    assert "download failed" in results["pi"].reason
    assert "FINAL RESULTS" in output and "Retry unresolved work" in output


def test_failed_provider_does_not_block_existing_capability():
    tasks = [Task("node", "", provides=("node",)), Task("pi", "", needs=("node",))]
    backend = FakeBackend(("node",), results={"node": Result("node", "failed")})
    _, results, _ = run(tasks, backend)
    assert results["pi"].outcome == "changed"


def test_manual_and_warnings_are_not_lost_in_summary():
    backend = FakeBackend(
        probes={"ssh": Result("ssh", "manual", "preserve config", "Review sshd -T")},
        results={"other": Result("other", "changed", warnings=["advisory"])},
    )
    code, results, output = run([Task("ssh", ""), Task("other", "")], backend)
    assert code == 1 and results["other"].outcome == "changed"
    assert output.count("advisory") == 2 and output.count("preserve config") == 2


def test_warning_alone_is_success():
    code, _, output = run(
        [Task("tool", "")],
        FakeBackend(results={"tool": Result("tool", "changed", warnings=["FYI"])}),
    )
    assert code == 0 and "WARNING: FYI" in output


def test_postcheck_failure_is_not_success():
    backend = FakeBackend(probes={"task": Result("task", "pending", "not configured")})
    code, results, _ = run([Task("task", "")], backend)
    assert code == 1 and results["task"].outcome == "failed"


def test_resource_failure_blocks_consumers_but_not_other_resources():
    tasks = [
        Task("apt", "", resources=("packages",)),
        Task("more", "", resources=("packages",)),
        Task("local", ""),
    ]
    code, results, _ = run(tasks, FakeBackend(results={"apt": Result("apt", "failed")}))
    assert (
        code == 1 and results["more"].outcome == "blocked" and results["local"].outcome == "changed"
    )


def test_health_probe_can_unblock_resource():
    tasks = [Task("apt", "", resources=("packages",)), Task("more", "", resources=("packages",))]
    _, results, _ = run(tasks, FakeBackend(("packages",), results={"apt": Result("apt", "failed")}))
    assert results["more"].outcome == "changed"


def test_readonly_never_applies_or_checks_install_prerequisites():
    backend = FakeBackend()
    code, results, _ = run([Task("task", "", needs=("missing",))], backend, readonly=True)
    assert code == 0 and results["task"].outcome == "pending"
    assert backend.calls == [("task", "probe")]


def test_satisfied_task_does_not_need_install_prerequisites():
    backend = FakeBackend(probes={"task": Result("task", "satisfied")})
    code, _, _ = run([Task("task", "", needs=("missing",))], backend)
    assert code == 0 and backend.calls == [("task", "probe")]


def test_fail_fast_names_unattempted_tasks():
    backend = FakeBackend(results={"bad": Result("bad", "failed")})
    code, results, _ = run([Task("bad", ""), Task("next", "")], backend, fail_fast=True)
    assert code == 1 and results["next"].outcome == "blocked"
    assert not any(name == "next" for name, _ in backend.calls)


def test_check_failure_continues_without_attempting_mutation():
    backend = FakeBackend(probes={"unknown": Result("unknown", "failed", "inspection failed")})
    _, results, _ = run([Task("unknown", ""), Task("other", "")], backend)
    assert results["other"].outcome == "changed"
    assert ("unknown", "apply") not in backend.calls


def test_declined_provider_blocks_only_dependents():
    tasks = [
        Task("provider", "", provides=("cap",)),
        Task("dependent", "", needs=("cap",)),
        Task("other", ""),
    ]
    runner = Runner(tasks, FakeBackend(), confirm=lambda t: t.id != "provider", emit=lambda _: None)
    assert runner.run() == 1
    assert [r.outcome for r in runner.results] == ["skipped", "blocked", "changed"]


@pytest.mark.parametrize(
    "tasks",
    [
        [Task("a", ""), Task("a", "")],
        [Task("a", "", ("b",), ("a",)), Task("b", "", ("a",), ("b",))],
        [Task("a", "", provides=("same",)), Task("b", "", provides=("same",))],
    ],
)
def test_invalid_graph_rejected(tasks):
    with pytest.raises(ValueError):
        validate(tasks)


def test_only_is_literal_and_with_deps_is_topological():
    tasks = [Task("consumer", "", ("cap",)), Task("provider", "", provides=("cap",))]
    assert [t.id for t in select(tasks, {}, "consumer")] == ["consumer"]
    assert [t.id for t in select(tasks, {}, "consumer", True)] == ["provider", "consumer"]
    with pytest.raises(ValueError):
        select(tasks, {}, "missing")


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"task": "other"},
        {"task": "x", "outcome": "satisfied"},
        dict(Result("x", "satisfied").to_dict(), warnings="not a list"),
        dict(Result("x", "satisfied").to_dict(), outcome=[]),
    ],
)
def test_malformed_result_is_rejected(value):
    with pytest.raises(ValueError):
        Result.decode(value, "x")


def test_worker_creates_only_fixture_directories_and_repeat_is_satisfied(tmp_path):
    backend = Backend("debian", tmp_path, "fixture", environment(tmp_path, {}), {})
    task = Task("directories", "packages")
    assert backend.call(task, "probe").outcome == "pending"
    assert backend.call(task, "apply").outcome == "changed"
    before = (tmp_path / "tools").stat().st_mtime_ns
    assert backend.call(task, "probe").outcome == "satisfied"
    assert (tmp_path / "tools").stat().st_mtime_ns == before
    assert {x.name for x in tmp_path.iterdir()} == {"projects", "tools"}


def test_worker_unknown_task_is_a_reported_failure(tmp_path):
    backend = Backend("debian", tmp_path, "fixture", environment(tmp_path, {}), {})
    assert backend.call("unknown", "apply").outcome == "failed"


def test_explicit_environment_does_not_inherit_profiles_or_installer_knobs(tmp_path):
    env = environment(
        tmp_path,
        {
            "BASH_ENV": "/danger",
            "PYTHONPATH": "/danger",
            "NODE_VERSION": "999",
            "HOME": "/wrong",
            "NVM_DIR": str(tmp_path / "custom"),
        },
    )
    assert not {"BASH_ENV", "PYTHONPATH", "NODE_VERSION"} & env.keys()
    assert env["HOME"] == str(tmp_path) and env["NVM_DIR"] == str(tmp_path / "custom")


def test_journal_resume_checks_identity_and_never_stores_key(tmp_path):
    journal = Journal(
        tmp_path / "state", "debian", "fixture", tmp_path, ["directories"], {"key": "private-input"}
    )
    journal.active("directories", "apply")
    journal.finish(130)
    value = json.loads(journal.path.read_text())
    assert value["active"]["phase"] == "apply"
    assert "private-input" not in journal.path.read_text()
    assert journal.path.stat().st_mode & 0o777 == 0o600
    resumed = Journal(
        tmp_path / "state",
        "debian",
        "fixture",
        tmp_path,
        ["directories"],
        {"key": "private-input"},
        journal.id,
    )
    assert resumed.data["results"] == []
    with pytest.raises(ValueError):
        Journal(
            tmp_path / "state",
            "macos",
            "fixture",
            tmp_path,
            ["directories"],
            {"key": "private-input"},
            journal.id,
        )


def test_lock_rejects_competing_run_and_symlink(tmp_path):
    path = tmp_path / "lock"
    with apply_lock(path), pytest.raises(ValueError), apply_lock(path):
        pass
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError), apply_lock(link):
        pass


def test_cancellation_stops_without_starting_another_task():
    class CancelBackend(FakeBackend):
        def call(self, task, phase):
            if phase == "apply":
                raise KeyboardInterrupt
            return super().call(task, phase)

    backend = CancelBackend()
    code, results, output = run([Task("current", ""), Task("next", "")], backend)
    assert code == 130 and not results
    assert not any(name == "next" for name, _ in backend.calls)
    assert "Cancelled" in output


def test_declining_manual_task_retains_its_diagnostic():
    diagnostic = Result(
        "ssh",
        "manual",
        "existing config is not hardened",
        "Review sshd -T",
        ["password authentication remains enabled"],
    )
    output = []
    runner = Runner(
        [Task("ssh", "")],
        FakeBackend(probes={"ssh": diagnostic}),
        confirm=lambda _: False,
        emit=output.append,
    )
    assert runner.run() == 0
    result = runner.results[0]
    assert result.outcome == "skipped" and diagnostic.reason in result.reason
    assert result.warnings == diagnostic.warnings and result.action == diagnostic.action
    assert diagnostic.reason in output[0]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "broken JSON",
        {"task": "wrong"},
        dict(Result("directories", "satisfied").to_dict(), outcome=[]),
    ],
)
def test_controller_reports_missing_or_malformed_worker_result(tmp_path, monkeypatch, payload):
    import io
    from pathlib import Path

    from setup_core import backend as backend_module

    class Process:
        def __init__(self, args, **kwargs):
            self.stdout = io.StringIO("ordinary diagnostic output is not a result\n")
            if payload is not None:
                Path(args[-1]).write_text(
                    payload if isinstance(payload, str) else json.dumps(payload)
                )

        def wait(self):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(backend_module.subprocess, "Popen", Process)
    backend = Backend("debian", tmp_path, "fixture", environment(tmp_path, {}), {})
    assert backend.call("directories", "probe").outcome == "failed"


def test_missing_providers_are_transitive_and_skip_available_or_selected():
    tasks = [
        Task("sudo", "", provides=("admin",)),
        Task("git", "", needs=("admin",), provides=("git",)),
        Task("node", "", provides=("node",)),
        Task("pi", "", needs=("node",)),
        Task("firstmate", "", needs=("git",)),
    ]
    selected = select(tasks, {}, "pi,firstmate")
    asked = []

    def available(capability, provider):
        asked.append(capability)
        return capability == "node"

    assert missing_providers(tasks, selected, available) == [
        ("firstmate", "git", "git"),
        ("git", "admin", "sudo"),
    ]
    assert asked == ["node", "git", "admin"]
    # Already selected providers are never offered.
    selected = select(tasks, {}, "firstmate,git")
    assert missing_providers(tasks, selected, lambda c, p: False) == [("git", "admin", "sudo")]


def offer_tasks():
    return [
        Task("sudo", "", provides=("admin",)),
        Task("git", "", needs=("admin",), provides=("git",)),
        Task("node", "", provides=("node",)),
        Task("pi", "", needs=("node",)),
        Task("firstmate", "", needs=("git",)),
    ]


def offer(only, backend, replies=None):
    from setup_core.cli import offer_prerequisites

    tasks = offer_tasks()
    output, asked = [], []

    def ask(provider):
        asked.append(provider)
        return replies.pop(0)

    selected, added = offer_prerequisites(
        tasks, {}, select(tasks, {}, only), only, backend, output.append,
        ask if replies is not None else None,
    )
    return [t.id for t in selected], added, output, asked


def test_only_offers_missing_prerequisites_and_adds_accepted_ones():
    backend = FakeBackend(probes={"sudo": Result("sudo", "pending")})
    selected, added, output, asked = offer("pi,firstmate", backend, ["", "y", ""])
    assert asked == ["node", "git", "sudo"]
    assert added == ["node", "git", "sudo"]
    assert selected == ["sudo", "git", "node", "pi", "firstmate"]
    assert "pi needs node, which is not available; node provides it" in output


def test_declined_prerequisite_drops_what_only_it_needed():
    backend = FakeBackend(probes={"sudo": Result("sudo", "pending")})
    selected, added, output, asked = offer("firstmate", backend, ["n"])
    assert asked == ["git"] and added == [] and selected == ["firstmate"]
    assert not any("sudo" in line for line in output)


def test_prerequisites_of_done_tasks_and_done_providers_are_not_offered():
    # pi is already installed; sudo is done though admin has no cached login.
    backend = FakeBackend(
        probes={"pi": Result("pi", "satisfied"), "sudo": Result("sudo", "satisfied")}
    )
    selected, added, output, asked = offer("pi,git", backend, [])
    assert output == [] and asked == [] and selected == ["git", "pi"]


def test_noninteractive_only_names_prerequisites_and_stays_literal():
    backend = FakeBackend(probes={"sudo": Result("sudo", "pending")})
    selected, added, output, asked = offer("pi", backend)
    assert selected == ["pi"] and added == []
    assert output == [
        "pi needs node, which is not available; node provides it",
        "Add --with-deps to include them",
    ]
