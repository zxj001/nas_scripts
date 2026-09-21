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
