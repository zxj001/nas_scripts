"""Helpers shared by the test modules."""

import os
from pathlib import Path

# A filename that isn't valid UTF-8, like the ones found in the Anime folder.
BAD_NAME = os.fsdecode(b"bad\xda.mkv")


def make_file(path: Path, size: int = 1024) -> Path:
    # Sparse, so multi-GB files cost no disk space.
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(size)
    return path
