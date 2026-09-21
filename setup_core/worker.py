"""A fresh process for every probe/apply/capability call."""

import importlib
import json
import sys
from pathlib import Path

from setup_core.commands import redact
from setup_core.context import Context, environment
from setup_core.model import Result
from setup_core.profiles import registry
from setup_core.state import atomic_json


def work(request):
    profile, name, phase = request["profile"], request["task"], request["phase"]
    home = Path(request["home"])
    ctx = Context(
        profile,
        home,
        request["user"],
        environment(home, request["env"]),
        phase,
        request.get("inputs", {}),
        task_id=name,
    )
    try:
        if phase in {"capability", "health"}:
            from setup_core import capabilities

            okay = getattr(capabilities, "available" if phase == "capability" else "health")(
                ctx, name
            )
            return ctx.result(
                "satisfied" if okay else "pending", "" if okay else f"{name} is unavailable"
            )
        task = next(t for t in registry(profile)[0] if t.id == name)
        module = importlib.import_module("setup_tasks." + task.module)
        result = getattr(module, phase)(ctx, task)
        return result
    except Exception as error:  # Task boundary must report any failure.
        return ctx.result(
            "failed", redact(f"{type(error).__name__}: {error}"), "Review the task log and retry"
        )


def main():
    request_path, result_path = map(Path, sys.argv[1:])
    request = json.loads(request_path.read_text())
    result = work(request)
    Result.decode(result.to_dict(), request["task"])
    atomic_json(result_path, result.to_dict())


if __name__ == "__main__":
    main()
