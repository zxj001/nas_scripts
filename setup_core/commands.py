"""One command boundary: explicit arguments, environment and cancellation."""

import os
import re
import signal
import subprocess
from dataclasses import dataclass


class CommandError(RuntimeError):
    pass


def redact(text):
    text = re.sub(r"https?://[^\s]+", "[URL omitted]", text)
    return re.sub(
        r"(?i)(token|password|secret|authorization)([=: ]+)[^\s]+", r"\1\2[redacted]", text
    )


@dataclass
class Output:
    returncode: int
    stdout: str
    stderr: str = ""


def execute(args, *, env, allowed=(0,), interactive=False, timeout=None, emit=False, split=False):
    args = [str(x) for x in args]
    # Worker shares the foreground terminal for intentional authentication. It
    # does not put sign-in URLs, tokens, or key input in the structured journal.
    # split keeps stderr out of output that callers parse or write back (a
    # sudo or crontab warning is not configuration, JSON or a file's content).
    errors = ""
    if interactive:
        with open("/dev/tty", "r+") as terminal:
            rc = subprocess.call(args, env=env, stdin=terminal, stdout=terminal, stderr=terminal)
        output = ""
    else:
        process = subprocess.Popen(
            args,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if split else subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        try:
            # Bounded probe/download calls use communicate; installer calls can
            # stream without collecting an unbounded amount of output in memory.
            if timeout is not None or split:
                output, errors = process.communicate(timeout=timeout)
                errors = errors or ""
                if emit:
                    print(redact(output), end="", flush=True)
            else:
                chunks, size = [], 0
                for line in process.stdout:
                    if emit:
                        print(redact(line), end="", flush=True)
                    if size < 1024 * 1024:
                        chunks.append(line)
                        size += len(line)
                output = "".join(chunks)
                process.wait()
            rc = process.returncode
        except KeyboardInterrupt:
            # apt/dpkg may still be finishing a transaction. Give the command an
            # interrupt and drain it; never force-kill a package database writer.
            package = any(os.path.basename(arg) in {"apt-get", "dpkg", "brew"} for arg in args[:3])
            previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                process.send_signal(signal.SIGINT)
                if package:
                    print("Waiting for the package transaction to stop safely…", flush=True)
                    process.communicate()
                else:
                    try:
                        process.communicate(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
            finally:
                signal.signal(signal.SIGINT, previous)
            raise
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
    if rc not in allowed:
        # Never put command arguments (which can include secrets) in diagnostics.
        raise CommandError(
            f"{os.path.basename(args[0])} exited {rc}: {redact((output + errors)[-1500:]).strip()}"
        )
    return Output(rc, output, errors)
