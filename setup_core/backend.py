"""Worker process lifecycle. Stdout is not a result protocol."""

import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from setup_core.model import Result
from setup_core.state import atomic_json


class Backend:
    def __init__(self, profile, home, user, env, inputs, journal=None, output=None):
        self.context = {
            "profile": profile,
            "home": str(home),
            "user": user,
            "env": env,
            "inputs": inputs,
        }
        self.journal = journal
        self.output = output or sys.stdout
        self.sequence = 0

    def call(self, task, phase):
        name = task if isinstance(task, str) else task.id
        if self.journal:
            self.journal.active(name, phase)
        with tempfile.TemporaryDirectory(prefix="nas-setup-worker-") as temporary:
            directory = Path(temporary)
            request, result = directory / "request.json", directory / "result.json"
            atomic_json(request, dict(self.context, task=name, phase=phase))
            # Works both from source and from the self-contained zipapp.
            package_root = Path(__file__).resolve().parent.parent
            if str(package_root).endswith(".pyz"):
                args = [
                    sys.executable,
                    "-I",
                    str(package_root),
                    "--worker",
                    str(request),
                    str(result),
                ]
            else:
                args = [
                    sys.executable,
                    "-I",
                    "-c",
                    "import sys; sys.path.insert(0, sys.argv.pop(1)); from setup_core.worker import main; main()",
                    str(package_root),
                    str(request),
                    str(result),
                ]
            self.sequence += 1
            log_path = (
                self.journal.directory / f"{self.journal.id}-{self.sequence:03d}-{name}.log"
                if self.journal
                else None
            )
            process = subprocess.Popen(
                args,
                env=self.context["env"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
            )
            log = None
            try:
                if log_path:
                    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    log = os.fdopen(fd, "w")
                for line in process.stdout:
                    if phase == "apply":
                        print(line, end="", flush=True, file=self.output)
                    if log:
                        log.write(line)
                rc = process.wait()
                if rc != 0 or not result.is_file():
                    return Result(
                        name,
                        "failed",
                        f"{phase} worker exited {rc} without a valid result",
                        "Review logs and retry; no success was recorded",
                    )
                return Result.decode(json.loads(result.read_text()), name)
            except KeyboardInterrupt:
                # Worker/command children remain in the foreground process group,
                # so terminal Ctrl-C reaches them too. Never start another task.
                process.send_signal(signal.SIGINT)
                process.wait()
                raise
            except (ValueError, OSError) as error:
                return Result(name, "failed", f"invalid {phase} result: {error}")
            finally:
                if log:
                    log.close()
                if process.poll() is None:
                    process.terminate()
                    process.wait()
