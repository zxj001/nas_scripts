"""Atomic writes with durable recovery records and explicit commit/rollback."""

import base64
import json
import sys

# Arguments are data. This small stdlib-only helper runs with privilege only
# for the file operation, never for the whole controller or user task graph.
PROGRAM = r"""
import base64, hashlib, json, os, pathlib, stat, sys, tempfile
op, target, encoded = sys.argv[1:]
p = pathlib.Path(target); data = base64.b64decode(encoded)
record = p.with_name(p.name + '.nas-setup-recovery')
def regular(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise SystemExit('refusing non-regular configuration/recovery file: ' + str(path))
def publish(path, content, mode, uid=None, gid=None, create=False):
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(content); f.flush(); os.fsync(f.fileno()); os.fchmod(f.fileno(), mode)
            if uid is not None: os.fchown(f.fileno(), uid, gid)
        if create: os.link(name, path)  # Never replace a concurrent operator write.
        else: os.replace(name, path)
    finally: pathlib.Path(name).unlink(missing_ok=True)
def digest(content): return None if content is None else hashlib.sha256(content).hexdigest()
regular(p); regular(record)
current = p.read_bytes() if p.exists() else None
saved = json.loads(record.read_text()) if record.exists() else None
if saved:
    if record.stat().st_uid != os.geteuid() or record.stat().st_mode & 0o077:
        raise SystemExit('unsafe recovery record; inspect manually')
    prior = None if saved['before'] is None else base64.b64decode(saved['before'])
    if digest(current) not in (saved['after'], digest(prior)):
        raise SystemExit('operator changed an interrupted config; preserve it and reconcile recovery manually')
    if op == 'commit':
        record.unlink(); print(json.dumps({'changed': False})); raise SystemExit(0)
    # Reconcile an interrupted transaction before attempting its next write.
    if prior is None: p.unlink(missing_ok=True)
    else: publish(p, prior, saved['mode'], saved['uid'], saved['gid'])
    record.unlink(); current = prior
if op in ('commit', 'rollback'):
    print(json.dumps({'changed': False})); raise SystemExit(0)
if op == 'create' and current not in (None, data):
    raise SystemExit('existing operator configuration requires manual review: ' + target)
if current == data:
    print(json.dumps({'changed': False})); raise SystemExit(0)
p.parent.mkdir(parents=True, exist_ok=True)
info = p.stat() if current is not None else None
saved = {'before': None if current is None else base64.b64encode(current).decode(),
         'after': digest(data), 'mode': stat.S_IMODE(info.st_mode) if info else 0o644,
         'uid': info.st_uid if info else os.geteuid(), 'gid': info.st_gid if info else os.getegid()}
# O_EXCL protects an existing recovery record. Full record is fsynced before
# publication; the machine apply lock serializes cooperating setup processes.
fd = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'w') as f:
    json.dump(saved, f); f.flush(); os.fsync(f.fileno())
if op == 'create' and p.exists():
    if p.read_bytes() != data: raise SystemExit('configuration changed before publication')
else:
    publish(p, data, saved['mode'], saved['uid'], saved['gid'], create=(op == 'create'))
print(json.dumps({'changed': True}))
"""


class ManualConfig(RuntimeError):
    pass


def recovery_path(path):
    return path.with_name(path.name + ".nas-setup-recovery")


class Change:
    def __init__(self, ctx, path, content, replace=False):
        self.ctx, self.path = ctx, str(path)
        if (
            not replace
            and path.exists()
            and not recovery_path(path).exists()
            and (path.is_symlink() or ctx.read(path, privileged=True) != content)
        ):
            raise ManualConfig(f"preserving existing configuration: {path}")
        self.changed = self._call("replace" if replace else "create", content.encode())["changed"]

    def _call(self, operation, data=b""):
        output = self.ctx.run(
            sys.executable,
            "-I",
            "-c",
            PROGRAM,
            operation,
            self.path,
            base64.b64encode(data).decode(),
            privileged=True,
        )
        return json.loads(output.stdout)

    def rollback(self):
        self._call("rollback")

    def commit(self):
        self._call("commit")


def append_once(path, line, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() and line in path.read_text().splitlines():
        return
    with path.open("a") as stream:
        stream.write("\n" + line + "\n")
    path.chmod(mode)
