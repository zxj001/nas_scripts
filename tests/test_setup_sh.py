import subprocess
from pathlib import Path


def test_setup_entrypoints():
    subprocess.run(['bash', str(Path(__file__).with_name('test_setup.sh'))], check=True)
