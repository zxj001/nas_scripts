import shutil
import sys
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

# /tmp is tmpfs on the NAS, and filefrag/e4defrag need a real filesystem
# such as ext4, so tests work in the repo's (gitignored) tmp/ directory.
REPO_TMP = Path(__file__).resolve().parent.parent / "tmp"

# The scripts live in scripts/ and import each other as top-level modules.
sys.path.insert(0, str(REPO_TMP.parent / "scripts"))


@pytest.fixture
def repo_tmp() -> Iterator[Path]:
    """A fresh directory under the repo's tmp/, removed after the test."""
    REPO_TMP.mkdir(exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="test_", dir=REPO_TMP))
    yield path
    shutil.rmtree(path)
    try:
        REPO_TMP.rmdir()  # only succeeds once nothing else is in it
    except OSError:
        pass
