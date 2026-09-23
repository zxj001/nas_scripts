"""Public CLI shared by the machine and Proxmox entrypoints."""

import argparse
import json
import os
import platform
import pwd
import re
import subprocess
import sys
from pathlib import Path

from setup_core.backend import Backend
from setup_core.commands import terminal
from setup_core.context import environment
from setup_core.model import COMPLETE, missing_providers, select
from setup_core.profiles import registry
from setup_core.runner import Runner
from setup_core.state import Journal, apply_lock


def detect_profile(requested):
    if requested == "proxmox":
        if os.geteuid() != 0 or not Path("/etc/pve").is_dir():
            raise ValueError("Proxmox setup requires a Proxmox VE host and root")
        completed = subprocess.run(["pveversion"], capture_output=True, check=False)
        if completed.returncode:
            raise ValueError("pveversion could not verify the host")
        return "proxmox"
    if platform.system() == "Darwin":
        return "macos"
    release = platform.freedesktop_os_release()
    if release.get("ID") != "debian" or release.get("VERSION_ID") != "13":
        raise ValueError(
            "machine setup supports Debian 13 and macOS; Proxmox uses its separate entrypoint"
        )
    if Path("/etc/pve").is_dir():
        raise ValueError("use proxmox_setup.sh on the hypervisor")
    return "debian"


def parser():
    cli = argparse.ArgumentParser(
        description="Repeatable setup: continue independent work and report every unresolved outcome."
    )
    cli.add_argument("--profile", choices=("auto", "debian", "macos", "proxmox"), default="auto")
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument(
        "--status", action="store_true", help="read-only checks; never prompt or update"
    )
    mode.add_argument(
        "--plan",
        action="store_true",
        help="show selected tasks and prerequisites without probing or writing",
    )
    cli.add_argument(
        "--yes",
        action="store_true",
        help="accept selected changes; interactive authentication remains deferred",
    )
    cli.add_argument(
        "--only",
        help="comma-separated task/group names; unavailable prerequisites are named and, "
        "when interactive, offered",
    )
    cli.add_argument(
        "--with-deps", action="store_true", help="include providers for missing capabilities"
    )
    cli.add_argument(
        "--fail-fast", action="store_true", help="stop scheduling after the first failed task"
    )
    cli.add_argument(
        "--resume",
        metavar="RUN_ID",
        help="re-probe a previous run using the same selection and inputs",
    )
    cli.add_argument("--state-dir", type=Path, help="private local journal directory")
    cli.add_argument(
        "--json",
        action="store_true",
        help="write structured results to stdout; progress goes to stderr",
    )
    cli.add_argument("--ssh-key", help="one public key; may also be supplied in PVE_OPERATOR_KEY")
    cli.add_argument("--lan-route", default="192.168.1.0/24")
    cli.add_argument(
        "--widget-target",
        help="ShellFish Pro widget identifier this machine sends to (default: short "
        "hostname; '' for the one shared widget)",
    )
    cli.add_argument("--firstmate-dir", help="FirstMate checkout location (default: ~/firstmate)")
    return cli


def offer_prerequisites(tasks, aliases, selected, only, backend, emit, ask=None):
    """Name every unavailable prerequisite of a selected task that still has work.

    With ask (interactive), offer each; declining one also drops what only it
    would have needed. A prerequisite counts as present when its capability
    works now or its provider is done (sudo is done for a sudo-group member
    without cached credentials). Returns the new selection and the additions.
    """
    by_id = {t.id: t for t in tasks}

    def present(capability, provider):
        if backend.call(capability, "capability").outcome == "satisfied":
            return True
        return backend.call(by_id[provider], "probe").outcome in COMPLETE

    unfinished = [t for t in selected if backend.call(t, "probe").outcome == "pending"]
    missing = missing_providers(tasks, unfinished, present)
    wanted = {t.id for t in unfinished}
    added = []
    for consumer, capability, provider in missing:
        if consumer not in wanted:
            continue
        emit(f"{consumer} needs {capability}, which is not available; {provider} provides it")
        if ask is None:
            continue
        reply = ask(provider).strip().lower()
        if reply and not reply.startswith("y"):
            continue
        wanted.add(provider)
        added.append(provider)
    if added:
        return select(tasks, aliases, ",".join([only, *added])), added
    if missing and ask is None:
        emit("Add --with-deps to include them")
    return selected, added


def main():
    args = parser().parse_args()
    try:
        # It lands in a crontab line; identifiers are plain names.
        if args.widget_target is not None and not re.fullmatch(
            r"[A-Za-z0-9._-]*", args.widget_target
        ):
            raise ValueError("--widget-target takes letters, digits, '.', '_' or '-'")
        profile = (
            args.profile if args.plan and args.profile != "auto" else detect_profile(args.profile)
        )
        if args.profile not in ("auto", profile):
            raise ValueError("requested profile does not match this host")
        tasks, aliases = registry(profile)
        selected = select(tasks, aliases, args.only, args.with_deps)
        if args.plan:
            if args.json:
                plan = [
                    {"id": t.id, "needs": list(t.needs), "provides": list(t.provides)}
                    for t in selected
                ]
                print(json.dumps({"profile": profile, "tasks": plan}, indent=2))
                return 0
            for task in selected:
                print(f"{task.id:22} needs: {', '.join(task.needs) or 'none'}")
            return 0
        if args.resume and not re.fullmatch(r"[a-f0-9]{32}", args.resume):
            raise ValueError("invalid resume run id")
        home = Path.home().resolve()
        # The effective account, not $USER, which su without - leaves behind.
        user = pwd.getpwuid(os.geteuid()).pw_name
        # Refuse ambiguous sudo HOME/user combinations for machine configuration.
        if profile != "proxmox" and os.environ.get("SUDO_USER"):
            raise ValueError(
                "run setup as the target user, without sudo; individual commands elevate as needed"
            )
        env = environment(home, os.environ)
        # A terminal still asks for the sudo password under --yes; --yes only
        # answers the task prompts. --status never prompts at all.
        tty = False
        if not args.status:
            try:
                with terminal():
                    tty = True
            except OSError:
                pass
        interactive = tty and not args.yes

        def prompt(message):
            with terminal() as stream:
                stream.write(message)
                return stream.readline().strip()

        firstmate = args.firstmate_dir
        if firstmate is not None:
            # Checked before Path(), which turns "" into ".".
            if not firstmate.strip():
                raise ValueError("--firstmate-dir needs a checkout path")
            # Absolute, so git clone can never read the path as an option.
            firstmate = Path(firstmate).expanduser()
            firstmate = firstmate if firstmate.is_absolute() else Path.cwd() / firstmate
        inputs = {
            "ssh_key": args.ssh_key or os.environ.get("PVE_OPERATOR_KEY", ""),
            "lan_route": args.lan_route,
            "firstmate_dir": str(firstmate) if firstmate else "",
            "interactive": "1" if interactive else "0",
        }
        if args.widget_target is not None:
            inputs["widget_target"] = args.widget_target
        output = sys.stderr if args.json else sys.stdout

        def emit(message):
            print(message, file=output, flush=True)

        def confirm(task):
            hint = "Y/n" if task.default else "y/N"
            reply = prompt(f"Run {task.id}? [{hint}] ").lower()
            return reply.startswith("y") if reply else task.default

        if not args.status and not args.yes and not interactive:
            raise ValueError("no terminal: use --yes; authentication/input tasks will be deferred")

        def sudo_once():
            if any("admin" in t.needs for t in selected) and os.geteuid() != 0:
                # Failure does not abort: admin capability probes block only
                # privileged consumers and independent user tasks still run.
                import shutil

                if shutil.which("sudo", path=env["PATH"]):
                    subprocess.run(["sudo", "-v"], env=env, check=False)

        if tty:
            sudo_once()
        backend = Backend(profile, home, user, env, inputs, output=output)
        added = []
        if args.only and not args.with_deps:
            ask = (lambda provider: prompt(f"Add {provider}? [Y/n] ")) if interactive else None
            selected, added = offer_prerequisites(
                tasks, aliases, selected, args.only, backend, emit, ask
            )
            if added and tty:
                sudo_once()
        if interactive:
            if any(t.id == "ssh-keys" for t in selected) and not inputs["ssh_key"]:
                inputs["ssh_key"] = prompt(
                    "Optional operator public key (blank to use existing keys): "
                )
        if args.status:
            runner = Runner(selected, backend, readonly=True, emit=emit)
            code = runner.run()
        else:
            with apply_lock():
                directory = args.state_dir or home / ".local/state/nas-setup"
                # The interaction mode is not part of desired machine state.
                desired = {k: v for k, v in inputs.items() if k != "interactive"}
                journal = Journal(
                    directory, profile, user, home, [t.id for t in selected], desired, args.resume
                )
                backend.journal = journal
                emit("Run " + journal.id + " — selected: " + ", ".join(t.id for t in selected))
                runner = Runner(
                    selected,
                    backend,
                    yes=args.yes,
                    fail_fast=args.fail_fast,
                    confirm=confirm,
                    emit=emit,
                    journal=journal,
                )
                code = runner.run()
                journal.finish(code)
                emit("Journal: " + str(journal.path))
                if code and added:
                    # Resume compares selections, so name the accepted additions.
                    emit(
                        "Resume with --only "
                        + ",".join(t.id for t in selected)
                        + " and the same other options plus --resume "
                        + journal.id
                    )
                elif code:
                    emit("Resume with the same options plus --resume " + journal.id)
        if args.json:
            print(
                json.dumps(
                    {
                        "profile": profile,
                        "exit_code": code,
                        "results": [r.to_dict() for r in runner.results],
                    },
                    indent=2,
                )
            )
        return code
    except (ValueError, OSError) as error:
        print(f"setup: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
