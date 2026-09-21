#!/usr/bin/env python3
"""Build deterministic self-contained setup distribution and compatibility wrappers."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from setup_core import VERSION
from setup_core.distribution import build


def wrappers(check=False):
    template = (ROOT / "scripts/setup-launcher.sh.in").read_text()
    for filename, profile in (("setup.sh", "auto"), ("proxmox_setup.sh", "proxmox")):
        content = template.replace("@PROFILE@", profile).replace("@VERSION@", VERSION)
        path = ROOT / "scripts" / filename
        if check:
            if path.read_text() != content:
                raise SystemExit(f"stale generated wrapper: {path}")
        else:
            path.write_text(content)
            path.chmod(0o755)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    wrappers(args.check)
    print(build(ROOT, args.output))
