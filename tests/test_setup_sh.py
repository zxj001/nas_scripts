import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def test_setup_entrypoints():
    subprocess.run(['bash', str(HERE / 'test_setup.sh')], check=True)


# Proxmox is Linux; the adapter's awk/sed are GNU (macOS CI runs this file).
linux_only = pytest.mark.skipif(sys.platform != 'linux', reason='Proxmox adapter is Linux-only')


@linux_only
def test_pve_repos_adapter():
    """tests/test_pve_repos.sh: the Proxmox repository repair against stubbed fixtures."""
    subprocess.run(['bash', str(HERE / 'test_pve_repos.sh')], check=True)


@linux_only
def test_pve_repos_suite_catches_lying_check(tmp_path):
    """A check_repos that always says done (enterprise still enabled) must fail the suite."""
    script = (HERE.parent / 'setup_adapters/pve_repos.sh').read_text()
    mutant = script.replace('check_repos() {\n', 'check_repos() {\n    return 0\n', 1)
    assert mutant != script
    path = tmp_path / 'pve_repos.sh'
    path.write_text(mutant)
    result = subprocess.run(
        ['bash', str(HERE / 'test_pve_repos.sh')],
        env={**os.environ, 'PVE_REPOS_ADAPTER': str(path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert 'repos done with the enterprise repos enabled' in result.stderr
