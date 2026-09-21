import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "test_setup.sh"


def test_setup_sh() -> None:
    """tests/test_setup.sh: syntax, shellcheck, step registry, --status (setup.sh and proxmox_setup.sh)."""
    subprocess.run(["bash", str(SCRIPT)], check=True)


def test_proxmox_setup_sh() -> None:
    """tests/test_proxmox_setup.sh: proxmox_setup.sh steps against stubbed fixtures."""
    subprocess.run(["bash", str(HERE / "test_proxmox_setup.sh")], check=True)


def test_proxmox_suite_catches_lying_repos_check(tmp_path: Path) -> None:
    """A check_repos that always says done (enterprise still enabled) must fail the suite."""
    script = (HERE.parent / "scripts" / "proxmox_setup.sh").read_text()
    mutant = script.replace("check_repos() {\n", "check_repos() {\n    return 0\n", 1)
    assert mutant != script
    path = tmp_path / "proxmox_setup.sh"
    path.write_text(mutant)
    result = subprocess.run(
        ["bash", str(HERE / "test_proxmox_setup.sh")],
        env={**os.environ, "PVE_SETUP_SCRIPT": str(path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "repos done with the enterprise repos enabled" in result.stderr
