"""The task/result contract; no machine operations in this module."""

import re
from dataclasses import asdict, dataclass, field

OUTCOMES = {
    "pending",
    "satisfied",
    "changed",
    "failed",
    "blocked",
    "manual",
    "deferred",
    "skipped",
    "not-applicable",
}
COMPLETE = {"satisfied", "changed", "skipped", "not-applicable"}


@dataclass(frozen=True)
class Task:
    id: str
    module: str
    needs: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    default: bool = True
    interactive: bool = False


@dataclass
class Result:
    task: str
    outcome: str
    reason: str = ""
    action: str = ""
    warnings: list[str] = field(default_factory=list)
    unsafe: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def decode(cls, value, task):
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("invalid worker result fields")
        if any(not isinstance(value[k], str) for k in ("task", "outcome", "reason", "action")):
            raise ValueError("invalid worker text")
        if value["task"] != task or value["outcome"] not in OUTCOMES:
            raise ValueError("invalid worker task/outcome")
        for key in ("warnings", "unsafe"):
            if not isinstance(value[key], list) or not all(isinstance(x, str) for x in value[key]):
                raise ValueError("invalid worker diagnostics")
        return cls(**value)


def validate(tasks):
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)) or any(not re.fullmatch(r"[a-z][a-z0-9.-]*", x) for x in ids):
        raise ValueError("duplicate or invalid task identifier")
    providers = {}
    for task in tasks:
        for capability in task.provides:
            if capability in providers:
                raise ValueError(f"duplicate provider for {capability}")
            providers[capability] = task.id
    edges = {t.id: [providers[n] for n in t.needs if n in providers] for t in tasks}
    visited, active = set(), set()

    def visit(name):
        if name in active:
            raise ValueError(f"cyclic dependency at {name}")
        if name not in visited:
            active.add(name)
            for dependency in edges[name]:
                visit(dependency)
            active.remove(name)
            visited.add(name)

    for name in ids:
        visit(name)
    return providers


def select(tasks, aliases, only=None, with_deps=False):
    providers = validate(tasks)
    by_id = {t.id: t for t in tasks}
    selected = set()
    for name in only.split(",") if only is not None else by_id:
        expanded = aliases.get(name, (name,))
        for key in expanded:
            if key not in by_id:
                raise ValueError(f"unknown task: {name}; available: {', '.join(by_id)}")
            selected.add(key)
    if with_deps:

        def expand(name):
            for cap in by_id[name].needs:
                provider = providers.get(cap)
                if provider and provider not in selected:
                    selected.add(provider)
                    expand(provider)

        for name in tuple(selected):
            expand(name)
    ordered, seen = [], set()

    def order(name):
        if name in seen:
            return
        seen.add(name)
        for cap in by_id[name].needs:
            if providers.get(cap) in selected:
                order(providers[cap])
        ordered.append(by_id[name])

    for task in tasks:
        if task.id in selected:
            order(task.id)
    return ordered


def missing_providers(tasks, selected, available):
    """Unavailable prerequisites of a selection, for offering them.

    Returns (consumer, capability, provider) for each capability a selected
    task needs whose provider is not selected and which
    available(capability, provider) reports missing, then the same for those
    providers, in the order found.
    """
    providers = validate(tasks)
    by_id = {t.id: t for t in tasks}
    chosen = {t.id for t in selected}
    checked, found, queue = {}, [], list(selected)
    while queue:
        task = queue.pop(0)
        for capability in task.needs:
            provider = providers.get(capability)
            if not provider or provider in chosen:
                continue
            if capability not in checked:
                checked[capability] = available(capability, provider)
            if checked[capability]:
                continue
            chosen.add(provider)
            found.append((task.id, capability, provider))
            queue.append(by_id[provider])
    return found
