"""One deterministic artifact for local builds, launchers and tagged releases."""

import hashlib
import zipfile
from pathlib import Path


def bundle_bytes(root):
    import io

    if str(root).endswith(".pyz"):
        return Path(root).read_bytes()
    sources = {"__main__.py": (root / "setup_core/__main__.py").read_bytes()}
    for package in ("setup_core", "setup_tasks", "setup_adapters"):
        for path in sorted((root / package).glob("*")):
            if path.suffix in (".py", ".sh"):
                sources[str(path.relative_to(root))] = path.read_bytes()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in sorted(sources.items()):
            entry = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, content)
    return stream.getvalue()


def build(root, output):
    output.mkdir(parents=True, exist_ok=True)
    content = bundle_bytes(root)
    target = output / "setup.pyz"
    target.write_bytes(content)
    (output / "setup.pyz.sha256").write_text(hashlib.sha256(content).hexdigest() + "  setup.pyz\n")
    return target
