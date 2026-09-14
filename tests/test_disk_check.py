import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import disk_check
from disk_check import (
    ATA,
    BLOCK,
    DRIVE,
    NVME,
    PARTITION,
    DriveHealth,
    FilesystemUsage,
    FolderUsage,
    Objects,
)
from tests.helpers import make_file, write_script

UDISKS = "/org/freedesktop/UDisks2"

HEALTHY_ATA: Dict[str, Any] = {
    "SmartUpdated": 1789031323,
    "SmartFailing": False,
    "SmartNumBadSectors": 0,
    "SmartNumAttributesFailing": 0,
    "SmartNumAttributesFailedInThePast": 0,
    "SmartTemperature": 316.15,
    "SmartPowerOnSeconds": 21027600,
    "SmartSelftestStatus": "success",
}

HEALTHY_NVME: Dict[str, Any] = {
    "SmartUpdated": 1789348723,
    "SmartCriticalWarning": [],
    "SmartTemperature": 310,
    "SmartPowerOnHours": 78,
    "SmartSelftestStatus": "success",
}


def udisks_drive(
    device: str,
    ata: Optional[Dict[str, Any]] = None,
    nvme: Optional[Dict[str, Any]] = None,
    size: int = 20 * 10**12,
    rotation: int = 7200,
) -> Objects:
    """udisks objects for one drive, its whole-disk block device and a partition."""
    path = f"{UDISKS}/drives/{device}_drive"

    def block(name: str) -> Dict[str, Any]:
        return {"Device": list(f"/dev/{name}".encode()) + [0], "Drive": path}

    interfaces: Dict[str, Dict[str, Any]] = {
        DRIVE: {
            "Model": "ST22000DM000",
            "Serial": "ZXA0QFL5",
            "Size": size,
            "RotationRate": rotation,
        }
    }
    if ata is not None:
        interfaces[ATA] = ata
    if nvme is not None:
        interfaces[NVME] = nvme

    return {
        path: interfaces,
        f"{UDISKS}/block_devices/{device}": {BLOCK: block(device)},
        f"{UDISKS}/block_devices/{device}1": {BLOCK: block(f"{device}1"), PARTITION: {}},
    }


def only_drive(objects: Objects) -> DriveHealth:
    [drive] = disk_check.drive_health(objects)
    return drive


def healthy_fs(mount: str) -> FilesystemUsage:
    return FilesystemUsage(mount, "/dev/sda1", 1000, 100, 900, 5, 0)


def table_rows(output: str) -> List[List[str]]:
    return [line.split() for line in output.splitlines()]


# percent


@pytest.mark.parametrize(
    "part, whole, expected",
    [(0, 10, 0), (1, 200, 1), (50, 100, 50), (99, 100, 99), (100, 100, 100), (5, 0, 0)],
)
def test_percent_rounds_up_like_df(part: int, whole: int, expected: int) -> None:
    assert disk_check.percent(part, whole) == expected


# folders


def test_folder_usage_totals_each_directory(repo_tmp: Path) -> None:
    movies, anime = repo_tmp / "Movies", repo_tmp / "Anime"
    make_file(movies / "a.mkv", 100)
    make_file(movies / "sub" / "b.mkv", 200)
    make_file(anime / "c.mkv", 50)
    errors: List[str] = []

    usage = disk_check.folder_usage([str(movies), str(anime)], errors)

    assert usage == [FolderUsage(str(movies), 300, 2), FolderUsage(str(anime), 50, 1)]
    assert errors == []


def test_format_folders_adds_total_row() -> None:
    lines = disk_check.format_folders(
        [FolderUsage("/a", 1024**3, 2), FolderUsage("/b", 1024**3, 1000)]
    )

    assert lines[0] == "      SIZE      FILES  FOLDER"
    assert lines[2].split() == ["1.0", "GiB", "2", "/a"]
    assert lines[-1].split() == ["2.0", "GiB", "1,002", "Total"]


def test_format_folders_skips_total_for_one_folder() -> None:
    lines = disk_check.format_folders([FolderUsage("/a", 1, 1)])

    assert len(lines) == 3
    assert "Total" not in lines[-1]


# filesystems


def test_mount_point_finds_filesystem_root(repo_tmp: Path) -> None:
    mount = disk_check.mount_point(str(repo_tmp))

    assert os.path.ismount(mount)
    assert str(repo_tmp.resolve()).startswith(mount)
    assert os.stat(mount).st_dev == os.stat(repo_tmp).st_dev


def test_mount_point_of_root() -> None:
    assert disk_check.mount_point("/") == "/"


def test_block_device_is_none_for_virtual_filesystems() -> None:
    assert disk_check.block_device("/proc") is None


def test_block_device_names_real_device(repo_tmp: Path) -> None:
    name = disk_check.block_device(str(repo_tmp))
    if name is None:
        pytest.skip("repo isn't on a block device")

    assert os.path.exists(f"/dev/{name}")


def test_ext4_errors_reads_kernel_count(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(disk_check, "EXT4_SYSFS", str(repo_tmp))
    (repo_tmp / "sda1").mkdir()
    (repo_tmp / "sda1" / "errors_count").write_text("3\n")

    assert disk_check.ext4_errors("sda1") == 3
    assert disk_check.ext4_errors("sdb1") is None


def test_filesystem_usage_of_real_filesystem(repo_tmp: Path) -> None:
    fs = disk_check.filesystem_usage(disk_check.mount_point(str(repo_tmp)))

    assert fs.total > 0
    assert 0 < fs.used + fs.free <= fs.total
    assert 0 <= fs.inodes_percent <= 100


def fs_usage(
    used: int = 10, free: int = 90, inodes: int = 5, errors: Optional[int] = 0
) -> FilesystemUsage:
    return FilesystemUsage("/media/Drive1", "/dev/sda1", 100, used, free, inodes, errors)


@pytest.mark.parametrize("errors", [0, None])
def test_filesystem_problems_none_when_healthy(errors: Optional[int]) -> None:
    assert disk_check.filesystem_problems(fs_usage(errors=errors), 90) == []


def test_filesystem_problems_warns_when_nearly_full() -> None:
    assert disk_check.filesystem_problems(fs_usage(used=90, free=10), 90) == [
        "/media/Drive1 is 90% full (10 B free)"
    ]


def test_filesystem_problems_uses_warn_percent() -> None:
    fs = fs_usage(used=85, free=15)

    assert disk_check.filesystem_problems(fs, 90) == []
    assert len(disk_check.filesystem_problems(fs, 80)) == 1


def test_filesystem_problems_warns_when_inodes_run_low() -> None:
    assert disk_check.filesystem_problems(fs_usage(inodes=95), 90) == [
        "/media/Drive1 has used 95% of its inodes"
    ]


def test_filesystem_problems_reports_ext4_errors() -> None:
    [problem] = disk_check.filesystem_problems(fs_usage(errors=2), 90)

    assert "2 ext4 error(s) on /dev/sda1" in problem


def test_format_filesystems_row() -> None:
    tib = 1024**4
    lines = disk_check.format_filesystems(
        [
            FilesystemUsage("/media/Drive1", "/dev/sda1", 20 * tib, 9 * tib, 11 * tib, 1, 0),
            FilesystemUsage("/mnt/other", "-", 100, 50, 50, 0, None),
        ]
    )

    assert lines[2].split() == [
        "45%", "20.0", "TiB", "9.0", "TiB", "11.0", "TiB", "1%", "0", "/dev/sda1", "/media/Drive1"
    ]
    assert lines[3].split()[-3:] == ["-", "-", "/mnt/other"]


# read_udisks


def fake_busctl(monkeypatch: pytest.MonkeyPatch, directory: Path, body: str) -> None:
    script = write_script(directory / "busctl", body)
    monkeypatch.setattr(disk_check, "UDISKS_COMMAND", [script])


def test_read_udisks_unwraps_property_values(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = {
        "type": "a{oa{sa{sv}}}",
        "data": [
            {
                f"{UDISKS}/drives/X": {
                    DRIVE: {
                        "Size": {"type": "t", "data": 5},
                        "Model": {"type": "s", "data": "ST22000DM000"},
                    }
                }
            }
        ],
    }
    fake_busctl(monkeypatch, repo_tmp, f"cat <<'EOF'\n{json.dumps(output)}\nEOF\n")

    assert disk_check.read_udisks() == {
        f"{UDISKS}/drives/X": {DRIVE: {"Size": 5, "Model": "ST22000DM000"}}
    }


def test_read_udisks_reports_busctl_errors(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_busctl(monkeypatch, repo_tmp, "echo 'Unit udisks2.service not found.' >&2\nexit 1\n")

    with pytest.raises(OSError, match="udisks2.service not found"):
        disk_check.read_udisks()


def test_read_udisks_reports_unreadable_output(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_busctl(monkeypatch, repo_tmp, "echo 'not json'\n")

    with pytest.raises(OSError, match="could not understand"):
        disk_check.read_udisks()


def test_read_udisks_reports_missing_busctl(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(disk_check, "UDISKS_COMMAND", [str(repo_tmp / "missing")])

    with pytest.raises(OSError):
        disk_check.read_udisks()


def test_read_udisks_gives_up_when_udisks_hangs(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_busctl(monkeypatch, repo_tmp, "sleep 5\n")
    monkeypatch.setattr(disk_check, "UDISKS_TIMEOUT", 0.2)

    with pytest.raises(OSError, match="no answer from udisks"):
        disk_check.read_udisks()


def test_drive_health_with_real_udisks() -> None:
    try:
        objects = disk_check.read_udisks()
    except OSError as error:
        pytest.skip(f"udisks not available: {error}")

    for drive in disk_check.drive_health(objects):
        assert drive.device.startswith("/dev/")


# drive_health


def test_drive_health_healthy_ata_drive() -> None:
    drive = only_drive(udisks_drive("sda", ata=HEALTHY_ATA))

    assert drive == DriveHealth("/dev/sda", "ST22000DM000 (ZXA0QFL5)", 43, 5841, [])
    assert drive.status == "OK"


def test_drive_health_healthy_nvme_drive() -> None:
    drive = only_drive(udisks_drive("nvme0n1", nvme=HEALTHY_NVME, rotation=0))

    assert (drive.device, drive.temperature, drive.hours) == ("/dev/nvme0n1", 37, 78)
    assert drive.status == "OK"


@pytest.mark.parametrize(
    "changes, problem",
    [
        ({"SmartFailing": True}, "SMART predicts the drive will fail soon"),
        ({"SmartNumBadSectors": 8}, "8 bad sector(s)"),
        ({"SmartNumAttributesFailing": 1}, "1 SMART attribute(s) failing now"),
        ({"SmartNumAttributesFailedInThePast": 2}, "2 SMART attribute(s) failed in the past"),
        ({"SmartSelftestStatus": "error_read"}, "last SMART self-test result: error_read"),
        ({"SmartTemperature": 333.15}, "running hot at 60°C (limit 55°C)"),
    ],
)
def test_drive_health_ata_problems(changes: Dict[str, Any], problem: str) -> None:
    drive = only_drive(udisks_drive("sda", ata={**HEALTHY_ATA, **changes}))

    assert drive.status == "PROBLEM"
    assert len(drive.problems) == 1
    assert drive.problems[0].startswith(problem)


@pytest.mark.parametrize("status", ["aborted", "interrupted", "inprogress", ""])
def test_drive_health_ignores_self_tests_that_found_nothing(status: str) -> None:
    drive = only_drive(
        udisks_drive("sda", ata={**HEALTHY_ATA, "SmartSelftestStatus": status})
    )

    assert drive.status == "OK"


def test_drive_health_ignores_unknown_bad_sector_count() -> None:
    drive = only_drive(udisks_drive("sda", ata={**HEALTHY_ATA, "SmartNumBadSectors": -1}))

    assert drive.status == "OK"


def test_drive_health_nvme_critical_warning() -> None:
    smart = {**HEALTHY_NVME, "SmartCriticalWarning": ["spare", "readonly"]}
    drive = only_drive(udisks_drive("nvme0n1", nvme=smart, rotation=0))

    assert drive.problems == ["NVMe critical warning: spare, readonly"]


def test_drive_health_lets_ssds_run_hotter_than_hdds() -> None:
    ssd = udisks_drive("nvme0n1", nvme={**HEALTHY_NVME, "SmartTemperature": 333}, rotation=0)
    hdd = udisks_drive("sda", ata={**HEALTHY_ATA, "SmartTemperature": 333.15})

    assert only_drive(ssd).status == "OK"
    assert only_drive(hdd).status == "PROBLEM"


@pytest.mark.parametrize("ata", [None, {**HEALTHY_ATA, "SmartUpdated": 0}])
def test_drive_health_drive_without_smart_data(ata: Optional[Dict[str, Any]]) -> None:
    drive = only_drive(udisks_drive("sda", ata=ata))

    assert drive.status == "NO SMART"
    assert (drive.temperature, drive.hours, drive.problems) == (None, None, [])


def test_drive_health_skips_empty_bays() -> None:
    assert disk_check.drive_health(udisks_drive("sdc", size=0)) == []


def test_drive_health_sorts_by_whole_disk_device() -> None:
    objects = {
        **udisks_drive("sdb", ata=HEALTHY_ATA),
        **udisks_drive("nvme0n1", nvme=HEALTHY_NVME, rotation=0),
        **udisks_drive("sda", ata=HEALTHY_ATA),
    }

    assert [d.device for d in disk_check.drive_health(objects)] == [
        "/dev/nvme0n1",
        "/dev/sda",
        "/dev/sdb",
    ]


def test_format_drives_rows() -> None:
    lines = disk_check.format_drives(
        [
            DriveHealth("/dev/sda", "ST22000DM000 (ZXA0QFL5)", 43, 5841, []),
            DriveHealth("/dev/sdb", "ST22000DM000 (ZXA0X9MB)", None, None, [], has_smart=False),
        ]
    )

    assert lines[2].split() == ["OK", "43°C", "5,841", "/dev/sda", "ST22000DM000", "(ZXA0QFL5)"]
    assert lines[3].split()[:5] == ["NO", "SMART", "-", "-", "/dev/sdb"]


# main


@pytest.fixture
def healthy_system(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake every filesystem as 10% full, with one healthy drive."""
    monkeypatch.setattr(disk_check, "filesystem_usage", healthy_fs)
    monkeypatch.setattr(
        disk_check, "read_udisks", lambda: udisks_drive("sda", ata=HEALTHY_ATA)
    )


@pytest.mark.usefixtures("healthy_system")
def test_main_checks_main_directories_by_default(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    drive1, drive2 = repo_tmp / "Drive1", repo_tmp / "Drive2"
    make_file(drive1 / "a.mkv", 100)
    make_file(drive2 / "b.mkv", 200)
    make_file(drive2 / "c.mkv", 300)
    monkeypatch.setattr(disk_check, "MAIN_DIRECTORIES", [str(drive1), str(drive2)])

    assert disk_check.main([]) == 0

    out, err = capsys.readouterr()
    rows = table_rows(out)
    assert ["100", "B", "1", str(drive1)] in rows
    assert ["500", "B", "2", str(drive2)] in rows
    assert ["600", "B", "3", "Total"] in rows
    assert f"Scanning: {drive1}" in err
    assert out.endswith("All checks passed.\n")


@pytest.mark.usefixtures("healthy_system")
def test_main_checks_root_and_each_directorys_filesystem(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    disk_check.main([str(repo_tmp)])

    mounts = [row[-1] for row in table_rows(capsys.readouterr().out) if row[-2:-1] == ["/dev/sda1"]]
    assert mounts == list(dict.fromkeys(["/", disk_check.mount_point(str(repo_tmp))]))


@pytest.mark.usefixtures("healthy_system")
def test_main_shows_drive_health(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    disk_check.main([str(repo_tmp)])

    assert ["OK", "43°C", "5,841", "/dev/sda", "ST22000DM000", "(ZXA0QFL5)"] in table_rows(
        capsys.readouterr().out
    )


@pytest.mark.usefixtures("healthy_system")
def test_main_reports_failing_drive(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        disk_check,
        "read_udisks",
        lambda: udisks_drive("sda", ata={**HEALTHY_ATA, "SmartNumBadSectors": 8}),
    )

    assert disk_check.main([str(repo_tmp)]) == 1

    assert (
        "1 problem(s) found:\n  /dev/sda ST22000DM000 (ZXA0QFL5): 8 bad sector(s)\n"
        in capsys.readouterr().out
    )


@pytest.mark.usefixtures("healthy_system")
def test_main_warn_percent_flags_full_filesystems(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert disk_check.main([str(repo_tmp), "--warn-percent", "10"]) == 1

    assert "/ is 10% full (900 B free)" in capsys.readouterr().out


@pytest.mark.usefixtures("healthy_system")
def test_main_reports_missing_directory_but_still_checks_drives(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = repo_tmp / "Unplugged"

    assert disk_check.main([str(repo_tmp), str(missing)]) == 1

    out, err = capsys.readouterr()
    assert f"{missing}: not found - is the drive mounted?" in out
    assert "Drive health" in out
    assert f"Warning: skipping, not a directory: {missing}" in err


@pytest.mark.usefixtures("healthy_system")
def test_main_reports_when_udisks_is_unavailable(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unavailable() -> Objects:
        raise OSError("udisks2.service not found")

    monkeypatch.setattr(disk_check, "read_udisks", unavailable)

    assert disk_check.main([str(repo_tmp)]) == 1

    out = capsys.readouterr().out
    assert "could not read drive health from udisks: udisks2.service not found" in out
    assert "Drive health" not in out
