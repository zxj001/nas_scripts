"""Continue independent work; results never stand in for capability probes."""

from setup_core.model import COMPLETE, Result, validate


class Runner:
    def __init__(
        self,
        tasks,
        backend,
        *,
        yes=False,
        readonly=False,
        fail_fast=False,
        confirm=lambda task: True,
        emit=print,
        journal=None,
    ):
        self.tasks, self.backend = tasks, backend
        self.yes, self.readonly, self.fail_fast = yes, readonly, fail_fast
        self.confirm, self.emit, self.journal = confirm, emit, journal
        self.results = []
        self.unhealthy = set()
        self.providers = validate(tasks)

    def record(self, result):
        self.results.append(result)
        self.emit(f"{result.task:22} {result.outcome:15} {result.reason}")
        for warning in result.warnings:
            self.emit(f"  WARNING: {warning}")
        if result.action:
            self.emit(f"  Next: {result.action}")
        if self.journal:
            self.journal.record(result)

    def run(self):
        try:
            for task in self.tasks:
                result = self.attempt(task)
                self.record(result)
                if self.fail_fast and result.outcome == "failed":
                    for remaining in self.tasks[len(self.results) :]:
                        self.record(
                            Result(
                                remaining.id,
                                "blocked",
                                "not attempted because --fail-fast was selected",
                            )
                        )
                    break
        except KeyboardInterrupt:
            self.emit("\nCancelled; interrupted work will be re-probed on resume.")
            self.summary()
            return 130
        self.summary()
        if self.readonly:
            return int(any(r.outcome == "failed" for r in self.results))
        return int(any(r.outcome not in COMPLETE for r in self.results))

    def attempt(self, task):
        for resource in set(task.resources) & self.unhealthy:
            if self.backend.call(resource, "health").outcome == "satisfied":
                self.unhealthy.remove(resource)
            else:
                return Result(
                    task.id,
                    "blocked",
                    f"{resource} remains unsafe after an earlier failure",
                    "Repair the resource and resume",
                )
        probe = self.backend.call(task, "probe")
        if probe.outcome != "pending":
            if probe.outcome in {"manual", "deferred"} and not self.readonly and not self.yes:
                self.emit(f"{task.id}: {probe.reason}")
                if probe.action:
                    self.emit("  Next: " + probe.action)
                if not self.confirm(task):
                    return Result(
                        task.id,
                        "skipped",
                        "declined by the user; " + probe.reason,
                        probe.action,
                        probe.warnings,
                        probe.unsafe,
                    )
            return probe
        if self.readonly:
            return probe
        missing = []
        for capability in task.needs:
            result = self.backend.call(capability, "capability")
            if result.outcome != "satisfied":
                cause = next(
                    (r for r in reversed(self.results) if r.task == self.providers.get(capability)),
                    None,
                )
                missing.append(
                    f"{capability}: {cause.outcome + ' — ' + cause.reason if cause else result.reason}"
                )
        if missing:
            return Result(
                task.id,
                "blocked",
                "; ".join(missing),
                f"Resolve prerequisites or rerun --only {task.id} --with-deps",
            )
        if not self.yes and not self.confirm(task):
            return Result(task.id, "skipped", "declined by the user")
        applied = self.backend.call(task, "apply")
        if applied.outcome == "changed":
            verified = self.backend.call(task, "probe")
            if verified.outcome != "satisfied":
                applied = Result(
                    task.id,
                    "failed",
                    "post-check did not verify the desired state: " + verified.reason,
                    verified.action,
                    applied.warnings + verified.warnings,
                    list(task.resources),
                )
            else:
                applied.warnings = list(dict.fromkeys(applied.warnings + verified.warnings))
                applied.action = applied.action or verified.action
        if applied.outcome == "failed":
            self.unhealthy.update(applied.unsafe or task.resources)
        return applied

    def summary(self):
        self.emit("\nFINAL RESULTS")
        for result in self.results:
            self.emit(f"{result.task:22} {result.outcome:15} {result.reason}")
            for warning in result.warnings:
                self.emit(f"  WARNING: {warning}")
            if result.action:
                self.emit(f"  Next: {result.action}")
        retry = [r.task for r in self.results if r.outcome not in COMPLETE]
        if retry:
            self.emit("Retry unresolved work: --only " + ",".join(retry) + " --with-deps")
