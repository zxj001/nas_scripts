"""Public CLI shared by the machine and Proxmox entrypoints."""

import argparse
import getpass
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

from setup_core.backend import Backend
from setup_core.context import environment
from setup_core.model import select
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
        "--only", help="comma-separated task/group names; does not implicitly install dependencies"
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
    return cli


def main():
    args = parser().parse_args()
    try:
        profile = (
            args.profile if args.plan and args.profile != "auto" else detect_profile(args.profile)
        )
        if args.profile not in ("auto", profile):
            raise ValueError("requested profile does not match this host")
        tasks, aliases = registry(profile)
        selected = select(tasks, aliases, args.only, args.with_deps)
        if args.plan:
            for task in selected:
                print(f"{task.id:22} needs: {', '.join(task.needs) or 'none'}")
            return 0
        if args.resume and not re.fullmatch(r"[a-f0-9]{32}", args.resume):
            raise ValueError("invalid resume run id")
        home = Path.home().resolve()
        user = getpass.getuser()
        # Refuse ambiguous sudo HOME/user combinations for machine configuration.
        if profile != "proxmox" and os.environ.get("SUDO_USER"):
            raise ValueError(
                "run setup as the target user, without sudo; individual commands elevate as needed"
            )
        env = environment(home, os.environ)
        interactive = False
        if not args.yes and not args.status:
            try:
                with open("/dev/tty", "r+"):
                    interactive = True
            except OSError:
                pass

        def prompt(message):
            with open("/dev/tty", "r+") as terminal:
                terminal.write(message)
                terminal.flush()
                return terminal.readline().strip()

        inputs = {
            "ssh_key": args.ssh_key or os.environ.get("PVE_OPERATOR_KEY", ""),
            "lan_route": args.lan_route,
            "interactive": "1" if interactive else "0",
        }
        output = sys.stderr if args.json else sys.stdout

        def emit(message):
            print(message, file=output, flush=True)

        def confirm(task):
            hint = "Y/n" if task.default else "y/N"
            reply = prompt(f"Run {task.id}? [{hint}] ").lower()
            return reply.startswith("y") if reply else task.default

        if not args.status and not args.yes and not interactive:
            raise ValueError("no terminal: use --yes; authentication/input tasks will be deferred")
        if interactive:
            if any("admin" in t.needs for t in selected) and os.geteuid() != 0:
                # Failure does not abort: admin capability probes block only
                # privileged consumers and independent user tasks still run.
                import shutil

                if shutil.which("sudo", path=env["PATH"]):
                    subprocess.run(["sudo", "-v"], env=env, check=False)
            if any(t.id == "ssh-keys" for t in selected) and not inputs["ssh_key"]:
                inputs["ssh_key"] = prompt(
                    "Optional operator public key (blank to use existing keys): "
                )
        backend = Backend(profile, home, user, env, inputs, output=output)
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
                if code:
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
