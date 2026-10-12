"""Execute the standalone cleaner with redirected paths and stubbed host commands."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/proxmox_cleaner.sh"
KEY = "/usr/share/keyrings/proxmox-archive-keyring.gpg"
PUBLIC = "http://download.proxmox.com/debian/pve"
ENTERPRISE = "https://enterprise.proxmox.com/debian/pve"


def stanza(uri=PUBLIC, suite="trixie", component="pve-no-subscription", extra=""):
    return (
        f"Types: deb\nURIs: {uri}\nSuites: {suite}\nComponents: {component}\n"
        f"Signed-By: {KEY}\n{extra}"
    )


def entries(text):
    """Normalize generated deb822 output, including continued field values."""
    result = []
    for block in text.strip().split("\n\n"):
        fields = {}
        key = None
        for line in block.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            if line[0].isspace():
                fields[key] += " " + line.strip()
            else:
                key, value = line.split(":", 1)
                key = key.lower()
                fields[key] = value.strip()
        if fields:
            result.append(fields)
    return result


@pytest.fixture
def host(tmp_path):
    root = tmp_path / "host"
    sources = root / "etc/apt/sources.list.d"
    sources.mkdir(parents=True)
    (root / "etc/pve").mkdir()
    (root / "etc/os-release").write_text('VERSION_CODENAME="trixie"\n')
    key = root / KEY.lstrip("/")
    key.parent.mkdir(parents=True)
    key.write_text("fixture key\n")
    commands = tmp_path / "bin"
    commands.mkdir()
    stubs = {
        "id": 'echo "${ID_UID:-0}"',
        "pveversion": 'echo "${PVE_VERSION:-pve-manager/9.2.0/fixture}"',
        "runuser": 'test "${DENY_KEY:-0}" = 0 && test -r "$6"',
        "apt-get": 'echo "$*" >> "$PVE_CLEANER_PREFIX/calls"; exit "${APT_RC:-0}"',
    }
    for name, body in stubs.items():
        command = commands / name
        command.write_text("#!/bin/sh\n" + body + "\n")
        command.chmod(0o755)
    return (
        root,
        sources,
        {
            **os.environ,
            "PVE_CLEANER_PREFIX": str(root),
            "PATH": f"{commands}:{os.environ['PATH']}",
        },
    )


def run(host, **overrides):
    return subprocess.run(
        ["bash", str(SCRIPT)], env={**host[2], **overrides}, capture_output=True, text=True
    )


def snapshot(host):
    return {
        str(p.relative_to(host[0])): p.read_bytes()
        for p in (host[0] / "etc").rglob("*")
        if p.is_file()
    }


def assert_apt_parses(host):
    """The real APT consumer checks the resulting files offline on Linux CI."""
    if not shutil.which("apt-get"):
        return
    root = host[0]
    parser = root / "parser"
    for name in ("etc/apt.conf.d", "state/lists/partial", "cache", "log"):
        (parser / name).mkdir(parents=True, exist_ok=True)
    config = parser / "apt.conf"
    config.write_text(
        f'Dir "{parser}";\nDir::Etc "{parser}/etc";\n'
        f'Dir::Etc::sourcelist "{root}/etc/apt/sources.list";\n'
        f'Dir::Etc::sourceparts "{root}/etc/apt/sources.list.d";\n'
        'APT::Architecture "amd64";\nAPT::Architectures { "amd64"; };\n'
    )
    result = subprocess.run(
        [shutil.which("apt-get"), "--print-uris", "update"],
        env={**os.environ, "APT_CONFIG": str(config)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "enterprise.proxmox.com" not in result.stdout
    assert "download.proxmox.com/debian/pve/dists/trixie/InRelease" in result.stdout
    return result.stdout


def test_fresh_install_preserves_sources_and_keeps_ceph_updates(host):
    root, sources, _ = host
    original = stanza(ENTERPRISE, component="pve-enterprise")
    (sources / "pve-enterprise.sources").write_text(original)
    (sources / "ceph.sources").write_text(
        stanza(
            "https://enterprise.proxmox.com/debian/ceph-squid",
            component="enterprise",
            extra="Enabled: yes\n",
        )
    )
    result = run(host)
    assert result.returncode == 0, result.stderr
    assert entries((sources / "pve-enterprise.sources").read_text())[0]["enabled"] == "no"
    assert entries((sources / "ceph.sources").read_text())[0]["enabled"] == "no"
    generated = entries((sources / "proxmox.sources").read_text())
    assert [e["components"] for e in generated] == ["pve-no-subscription", "no-subscription"]
    assert generated[1]["uris"] == "http://download.proxmox.com/debian/ceph-squid"
    backups = list((root / "root").glob("apt-repos-backup-*"))
    assert len(backups) == 1
    assert (backups[0] / "sources.list.d/pve-enterprise.sources").read_text() == original
    assert (root / "calls").read_text() == "update -o APT::Update::Error-Mode=any\n"
    assert_apt_parses(host)
    before = snapshot(host)
    assert run(host).returncode == 0
    assert snapshot(host) == before
    assert len(list((root / "root").glob("apt-repos-backup-*"))) == 2


@pytest.mark.parametrize("extra", ["Enabled: no\n", "Enabled: false\n", "Types: deb-src\n"])
def test_disabled_or_source_only_repo_does_not_block_binary_source(host, extra):
    # Avoid duplicate Types fields for the source-only case.
    text = (
        stanza(extra=extra)
        if "Types" not in extra
        else stanza().replace("Types: deb", "Types: deb-src")
    )
    (host[1] / "existing.sources").write_text(text)
    assert run(host).returncode == 0
    assert entries((host[1] / "proxmox.sources").read_text())[0]["types"] == "deb"
    assert_apt_parses(host)


@pytest.mark.parametrize(
    "filename", ["old.sources.save", "old.sources.disabled", "bad name.sources"]
)
def test_apt_ignored_files_do_not_count(host, filename):
    (host[1] / filename).write_text(stanza())
    assert run(host).returncode == 0
    assert (host[1] / "proxmox.sources").exists()


@pytest.mark.parametrize("uri", [PUBLIC, PUBLIC + "/", PUBLIC.replace("http:", "https:")])
def test_active_continued_repo_recognized_without_duplicate(host, uri):
    text = (
        f"types: deb\nURIs:\n {uri}\nSuites:\n trixie\n"
        f"Components: other pve-no-subscription\nSigned-By: {KEY}\nenabled:\n yes\n"
    )
    (host[1] / "custom.sources").write_text(text)
    assert run(host).returncode == 0
    assert not (host[1] / "proxmox.sources").exists()
    assert (host[1] / "custom.sources").read_text() == text
    assert_apt_parses(host)


def test_custom_multistanza_files_and_legacy_sources_list(host):
    root, sources, _ = host
    unrelated = stanza("http://example.org/debian", component="main")
    combined = stanza(ENTERPRISE, component="pve-enterprise", extra="enabled:\n yes\n")
    (sources / "proxmox.sources").write_text(combined + "\n" + unrelated)
    legacy = f"\tdeb-src [signed-by={KEY}] {ENTERPRISE} trixie pve-enterprise\n"
    (root / "etc/apt/sources.list").write_text(legacy + "# leave comment\n")
    (sources / "sources.list").write_text(f"deb {ENTERPRISE} trixie pve-enterprise\n")
    assert run(host).returncode == 0
    output = entries((sources / "proxmox.sources").read_text())
    assert output[0]["enabled"] == "no"
    assert output[1] == entries(unrelated)[0]
    assert output[2]["components"] == "pve-no-subscription"
    assert (root / "etc/apt/sources.list").read_text() == "# " + legacy + "# leave comment\n"
    assert (sources / "sources.list").read_text().startswith("# deb ")
    assert_apt_parses(host)


def test_existing_legacy_public_source_not_duplicated(host):
    (host[0] / "etc/apt/sources.list").write_text(
        f"\tdeb [arch=amd64 signed-by={KEY}] {PUBLIC} trixie pve-no-subscription # note\n"
    )
    assert run(host).returncode == 0
    assert not (host[1] / "proxmox.sources").exists()
    assert_apt_parses(host)


@pytest.mark.parametrize(
    "extra", [{"ID_UID": "1000"}, {"PVE_VERSION": "pve-manager/8.4/fixture"}, {"DENY_KEY": "1"}]
)
def test_invalid_host_or_unreadable_key_refused_without_changes(host, extra):
    before = snapshot(host)
    assert run(host, **extra).returncode != 0
    assert snapshot(host) == before
    assert not (host[0] / "calls").exists()
    assert not (host[0] / "root").exists()


@pytest.mark.parametrize("condition", ["bookworm", "no-pve", "missing-key", "empty-key"])
def test_host_prerequisites_refused(host, condition):
    root = host[0]
    if condition == "bookworm":
        (root / "etc/os-release").write_text("VERSION_CODENAME=bookworm\n")
    elif condition == "no-pve":
        (root / "etc/pve").rmdir()
    elif condition == "missing-key":
        (root / KEY.lstrip("/")).unlink()
    else:
        (root / KEY.lstrip("/")).write_text("")
    before = snapshot(host)
    assert run(host).returncode != 0
    assert snapshot(host) == before


@pytest.mark.parametrize(
    "text",
    [
        stanza(suite="bookworm"),
        stanza(component="pve-test").replace(KEY, "/custom/key.gpg"),
    ],
)
def test_stale_or_conflicting_public_sources_refused_unchanged(host, text):
    (host[1] / "existing.sources").write_text(text)
    (host[1] / "pve-enterprise.sources").write_text(stanza(ENTERPRISE, component="pve-enterprise"))
    before = snapshot(host)
    assert run(host).returncode != 0
    assert snapshot(host) == before
    assert not (host[0] / "calls").exists()


@pytest.mark.parametrize("options", ["", "[signed-by=/custom/key.gpg] "])
def test_upgraded_host_legacy_public_source_is_accepted(host, options):
    """A PVE 8 style no-subscription line (own key or none) is valid; nothing to add."""
    legacy = f"deb {options}{PUBLIC} trixie pve-no-subscription\n"
    (host[1] / "pve-no-subscription.list").write_text(legacy)
    (host[1] / "pve-enterprise.sources").write_text(stanza(ENTERPRISE, component="pve-enterprise"))
    assert run(host).returncode == 0
    assert entries((host[1] / "pve-enterprise.sources").read_text())[0]["enabled"] == "no"
    assert (host[1] / "pve-no-subscription.list").read_text() == legacy
    assert not (host[1] / "proxmox.sources").exists()
    assert_apt_parses(host)


def test_mixed_uri_refusal_is_preflighted_before_any_rewrites(host):
    (host[1] / "a.sources").write_text(stanza(ENTERPRISE, component="pve-enterprise"))
    (host[1] / "z.sources").write_text(
        stanza(ENTERPRISE + " http://example.org/debian", component="pve-enterprise")
    )
    before = snapshot(host)
    result = run(host)
    assert result.returncode != 0
    assert "Mixed enterprise" in result.stderr
    assert snapshot(host) == before
    assert not (host[0] / "calls").exists()


def test_sources_list_backup_failure_aborts_before_changes(host):
    (host[0] / "etc/apt/sources.list").write_text("# existing\n")
    commands = Path(host[2]["PATH"].split(os.pathsep)[0])
    cp = commands / "cp"
    cp.write_text("#!/bin/sh\nexit 1\n")
    cp.chmod(0o755)
    before = snapshot(host)
    assert run(host).returncode != 0
    assert snapshot(host) == before
    assert not (host[0] / "calls").exists()


def test_apt_failure_is_reported_and_backup_retained(host):
    result = run(host, APT_RC="100")
    assert result.returncode == 100
    assert "Repository setup finished" not in result.stdout
    assert len(list((host[0] / "root").glob("apt-repos-backup-*"))) == 1
    assert (host[1] / "proxmox.sources").exists()


def test_missing_source_directory_and_empty_sources(host):
    host[1].rmdir()
    assert run(host).returncode == 0
    assert_apt_parses(host)


def test_commented_entries_and_other_uri_do_not_count(host):
    (host[0] / "etc/apt/sources.list").write_text(f"# deb {PUBLIC} trixie pve-no-subscription\n")
    other = stanza("http://example.org/debian", extra="Enabled: no\n")
    (host[1] / "unrelated.sources").write_text(other)
    assert run(host).returncode == 0
    assert (host[1] / "unrelated.sources").read_text() == other
    assert (host[1] / "proxmox.sources").exists()
    assert_apt_parses(host)


def test_historical_ceph_sources_stay_disabled(host):
    historical = stanza(
        "https://enterprise.proxmox.com/debian/ceph-reef",
        suite="bookworm",
        component="enterprise",
        extra="Enabled: no\n",
    )
    (host[1] / "ceph.sources").write_text(historical)
    assert run(host).returncode == 0
    assert (host[1] / "ceph.sources").read_text() == historical
    assert len(entries((host[1] / "proxmox.sources").read_text())) == 1


def test_wrong_suite_active_ceph_refused(host):
    (host[1] / "ceph.sources").write_text(
        stanza(
            "https://enterprise.proxmox.com/debian/ceph-reef",
            suite="bookworm",
            component="enterprise",
        )
    )
    before = snapshot(host)
    assert run(host).returncode != 0
    assert snapshot(host) == before


def test_existing_ceph_public_source_not_duplicated(host):
    ceph_uri = "http://download.proxmox.com/debian/ceph-squid"
    (host[1] / "ceph.sources").write_text(
        stanza("https://enterprise.proxmox.com/debian/ceph-squid", component="enterprise")
        + "\n"
        + stanza(ceph_uri, component="no-subscription")
    )
    assert run(host).returncode == 0
    assert len(entries((host[1] / "proxmox.sources").read_text())) == 1
    output = assert_apt_parses(host)
    if output is not None:
        assert "download.proxmox.com/debian/ceph-squid/dists/trixie/InRelease" in output
