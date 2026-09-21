#!/usr/bin/env bash
# Mutating checks are confined to a disposable systemd container.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
name="nas-setup-test-$$"
results="$(mktemp -d)"
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$results"; }
trap cleanup EXIT
docker build -t nas-setup-debian-test "$root/tests/integration"
docker run -d --name "$name" --privileged --cgroupns=host \
    --tmpfs /run --tmpfs /run/lock -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
    -v "$root:/work:ro" nas-setup-debian-test
for _attempt in {1..30}; do
    if docker exec "$name" systemctl is-active dbus >/dev/null 2>&1; then break; fi
    sleep 2
done
docker exec "$name" systemctl is-active dbus
# A real package install, file creation, unit mask, ssh enable and repeat run.
for pass in first second; do
    docker exec -e PYTHONDONTWRITEBYTECODE=1 "$name" bash /work/scripts/setup.sh \
        --yes --only download-tools,directories,git,dev-utilities,ssh,no-sleep --with-deps --json >"$results/$pass.json"
done
python3 - "$results" <<'PY'
import json,pathlib,sys
root=pathlib.Path(sys.argv[1])
first=json.loads((root/'first.json').read_text()); second=json.loads((root/'second.json').read_text())
assert first['exit_code']==second['exit_code']==0
assert any(r['outcome']=='changed' for r in first['results'])
assert all(r['outcome']=='satisfied' for r in second['results']), second
PY
# Missing dependencies are reported and independent work still completes.
set +e
docker exec -e PYTHONDONTWRITEBYTECODE=1 "$name" bash /work/scripts/setup.sh \
    --yes --only pi,directories --json >"$results/partial.json"
rc=$?
set -e
[ "$rc" = 1 ]
python3 - "$results/partial.json" <<'PY'
import json,sys
r={x['task']: x['outcome'] for x in json.load(open(sys.argv[1]))['results']}
assert r=={'directories':'satisfied','pi':'blocked'},r
PY
# Exercise the actual packaged controller, isolated worker discovery and resume.
docker exec "$name" python3 /work/scripts/build_setup.py --check --output /root/bundle
docker exec "$name" python3 -I /root/bundle/setup.pyz --yes --only directories --json >"$results/bundle.json"
run_id="$(docker exec "$name" python3 -c 'import json,pathlib; paths=pathlib.Path("/root/.local/state/nas-setup").glob("*.json"); print(json.loads(max(paths,key=lambda p:p.stat().st_mtime_ns).read_text())["run"])')"
docker exec "$name" python3 -I /root/bundle/setup.pyz --yes --only directories --resume "$run_id" --json >"$results/resume.json"
python3 - "$results" <<'PY'
import json,pathlib,sys
for name in ('bundle', 'resume'):
    result=json.loads((pathlib.Path(sys.argv[1])/(name+'.json')).read_text())
    assert result['exit_code']==0 and result['results'][0]['outcome']=='satisfied',result
PY
