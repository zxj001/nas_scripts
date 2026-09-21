import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "test_setup.sh"


def test_setup_sh() -> None:
    """tests/test_setup.sh: syntax, shellcheck, step registry, --status (setup.sh and proxmox_setup.sh)."""
    subprocess.run(["bash", str(SCRIPT)], check=True)
