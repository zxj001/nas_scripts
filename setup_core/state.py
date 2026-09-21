"""Restricted journals and a shared apply lock. Journals never bypass probes."""

import fcntl
import hashlib
import json
import os
import socket
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from setup_core import VERSION


def atomic_json(path, data):
    fd, temporary = tempfile.mkstemp(prefix=".result-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def private_directory(path):
    path = Path(path)
    # Reject symlink components rather than redirecting machine state elsewhere.
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise ValueError(f"state directory contains a symlink: {part}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid():
        raise ValueError("state directory belongs to another user")
    os.chmod(path, 0o700)
    return path


@contextmanager
def apply_lock(path=Path("/tmp/nas-setup-apply.lock")):
    # A non-writable common lock permits different users to take the same flock.
    # O_NOFOLLOW and ownership/type validation prevent a redirected lock file.
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDONLY | os.O_NOFOLLOW, 0o444)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        import stat

        if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:
            raise ValueError("unsafe setup lock file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("another setup apply is running") from error
        yield
    finally:
        os.close(fd)


class Journal:
    def __init__(self, directory, profile, user, home, selected, inputs, resume=None):
        self.directory = private_directory(directory)
        identity = {
            "host": socket.gethostname(),
            "user": user,
            "home": str(home),
            "profile": profile,
        }
        digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        if resume:
            old_path = self.directory / f"{resume}.json"
            if old_path.is_symlink():
                raise ValueError("unsafe resume journal")
            old = json.loads(old_path.read_text())
            if old["identity"] != identity or old["version"] != VERSION or old["inputs"] != digest:
                raise ValueError(
                    "resume identity/version/inputs changed; start a fresh run to re-plan"
                )
            if old["selected"] != selected:
                raise ValueError(
                    "resume selection changed; repeat the original --only/--with-deps selection"
                )
        self.id = uuid.uuid4().hex
        self.path = self.directory / f"{self.id}.json"
        self.data = {
            "run": self.id,
            "version": VERSION,
            "identity": identity,
            "inputs": digest,
            "selected": selected,
            "resume_of": resume,
            "started": time.time(),
            "results": [],
            "active": None,
        }
        self.save()

    def save(self):
        atomic_json(self.path, self.data)

    def active(self, task, phase):
        self.data["active"] = {"task": task, "phase": phase, "started": time.time()}
        self.save()

    def record(self, result):
        self.data["results"].append(result.to_dict())
        self.data["active"] = None
        self.save()

    def finish(self, code):
        self.data.update(exit_code=code, finished=time.time())
        self.save()
