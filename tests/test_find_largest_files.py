import os
from pathlib import Path
from typing import List, Sequence

import pytest

import find_largest_files as flf
from find_largest_files import FileEntry
from tests.helpers import BAD_NAME, make_file

DATA_DIR = Path(__file__).parent / "data" / "sample"

needs_non_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="root can read every directory"
)


def names(entries: Sequence[FileEntry]) -> List[str]:
    return [os.path.basename(entry.path) for entry in entries]


def listed_files(output: str) -> List[str]:
    """Filenames from the results table, in the order printed."""
    lines = output.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("-" * 10)) + 1
    return [line.rsplit("/", 1)[-1] for line in lines[start:] if line.strip()]


def lock(directory: Path) -> Path:
    make_file(directory / "hidden.mkv")
    directory.chmod(0)
    return directory


# scan


def test_scan_static_data() -> None:
    files, errors = flf.scan([str(DATA_DIR)])

    assert names(flf.largest(files))[0] == "file2.txt"
    assert sorted(names(files)) == ["file1.txt", "file2.txt", "file3.txt"]
    assert errors == []


def test_scan_finds_nested_files_across_directories(repo_tmp: Path) -> None:
    a = make_file(repo_tmp / "Drive1" / "a.mkv", 10)
    b = make_file(repo_tmp / "Drive2" / "sub" / "b.mkv", 20)

    files, errors = flf.scan([str(repo_tmp / "Drive1"), str(repo_tmp / "Drive2")])

    assert sorted(files) == [FileEntry(10, str(a)), FileEntry(20, str(b))]
    assert errors == []


def test_scan_no_recursive_skips_subdirectories(repo_tmp: Path) -> None:
    make_file(repo_tmp / "top.mkv")
    make_file(repo_tmp / "sub" / "nested.mkv")

    files, _ = flf.scan([str(repo_tmp)], recursive=False)

    assert names(files) == ["top.mkv"]


def test_scan_skips_symlinks(repo_tmp: Path) -> None:
    target = make_file(repo_tmp / "movie.mkv")
    (repo_tmp / "link.mkv").symlink_to(target)
    (repo_tmp / "broken.mkv").symlink_to(repo_tmp / "missing.mkv")

    files, errors = flf.scan([str(repo_tmp)])

    assert names(files) == ["movie.mkv"]
    assert errors == []


@needs_non_root
def test_scan_reports_unreadable_directories(repo_tmp: Path) -> None:
    make_file(repo_tmp / "ok.mkv")
    locked = lock(repo_tmp / "locked")
    try:
        files, errors = flf.scan([str(repo_tmp)])
    finally:
        locked.chmod(0o755)

    assert names(files) == ["ok.mkv"]
    assert errors == [str(locked)]


# largest


FILES = [FileEntry(5, "/b"), FileEntry(9, "/c"), FileEntry(5, "/a")]


def test_largest_sorts_biggest_first_then_by_path() -> None:
    assert flf.largest(FILES) == [
        FileEntry(9, "/c"),
        FileEntry(5, "/a"),
        FileEntry(5, "/b"),
    ]


@pytest.mark.parametrize(
    "top, expected", [(2, ["/c", "/a"]), (0, ["/c", "/a", "/b"]), (10, ["/c", "/a", "/b"])]
)
def test_largest_top(top: int, expected: List[str]) -> None:
    assert [entry.path for entry in flf.largest(FILES, top=top)] == expected


def test_largest_min_size() -> None:
    assert flf.largest(FILES, min_size=6) == [FileEntry(9, "/c")]


# format_table


def test_format_table_human_sizes() -> None:
    lines = flf.format_table([FileEntry(5 * 1024**3, "/media/a.mkv")])

    assert lines[0] == "      SIZE  FILE"
    assert lines[2] == "   5.0 GiB  /media/a.mkv"


def test_format_table_exact_bytes() -> None:
    lines = flf.format_table([FileEntry(5 * 1024**3, "/media/a.mkv")], exact_bytes=True)

    assert lines[2].split() == ["5368709120", "/media/a.mkv"]


def test_format_table_replaces_invalid_filename_bytes() -> None:
    lines = flf.format_table([FileEntry(1, "/media/" + BAD_NAME)])

    assert lines[2].endswith("/media/bad�.mkv")


# main


def test_main_scans_main_directories_by_default(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    drive1, drive2 = repo_tmp / "Drive1", repo_tmp / "Drive2"
    make_file(drive1 / "small.mkv", 100)
    make_file(drive2 / "big.mkv", 200)
    monkeypatch.setattr(flf, "MAIN_DIRECTORIES", [str(drive1), str(drive2)])

    assert flf.main([]) == 0

    out, err = capsys.readouterr()
    assert listed_files(out) == ["big.mkv", "small.mkv"]
    assert f"Scanning: {drive1}" in err
    assert f"Scanning: {drive2}" in err


def test_main_directory_argument_overrides_main_directories(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    make_file(repo_tmp / "default" / "a.mkv")
    make_file(repo_tmp / "chosen" / "b.mkv")
    monkeypatch.setattr(flf, "MAIN_DIRECTORIES", [str(repo_tmp / "default")])

    flf.main([str(repo_tmp / "chosen")])

    assert listed_files(capsys.readouterr().out) == ["b.mkv"]


def test_main_shows_top_20_by_default(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for i in range(25):
        make_file(repo_tmp / f"{i:02}.mkv", 100 + i)

    flf.main([str(repo_tmp)])

    assert listed_files(capsys.readouterr().out) == [
        f"{i:02}.mkv" for i in range(24, 4, -1)
    ]


def test_main_top_zero_lists_everything(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for i in range(25):
        make_file(repo_tmp / f"{i:02}.mkv", 100 + i)

    flf.main([str(repo_tmp), "-n", "0"])

    assert len(listed_files(capsys.readouterr().out)) == 25


def test_main_min_size_filters_small_files(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_file(repo_tmp / "big.mkv", 2 * 1024**3)
    make_file(repo_tmp / "small.mkv", 1024)

    flf.main([str(repo_tmp), "--min-size", "1"])

    assert listed_files(capsys.readouterr().out) == ["big.mkv"]


def test_main_bytes_shows_exact_sizes(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_file(repo_tmp / "a.mkv", 1536)

    flf.main([str(repo_tmp), "--bytes"])

    assert capsys.readouterr().out.splitlines()[-1].split()[0] == "1536"


def test_main_output_saves_list_to_file(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_file(repo_tmp / "media" / "a.mkv")
    saved = repo_tmp / "files.txt"

    assert flf.main([str(repo_tmp / "media"), "-o", str(saved)]) == 0

    out, err = capsys.readouterr()
    assert out == ""
    assert listed_files(saved.read_text()) == ["a.mkv"]
    assert f"Saved 1 file(s) to {saved}" in err


def test_main_output_reports_unwritable_file(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_file(repo_tmp / "a.mkv")
    saved = repo_tmp / "missing" / "files.txt"

    assert flf.main([str(repo_tmp), "-o", str(saved)]) == 1

    assert f"Error: could not write {saved}" in capsys.readouterr().err


def test_main_skips_missing_directories(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_file(repo_tmp / "Drive1" / "a.mkv")
    missing = repo_tmp / "Unplugged"

    assert flf.main([str(repo_tmp / "Drive1"), str(missing)]) == 0

    out, err = capsys.readouterr()
    assert listed_files(out) == ["a.mkv"]
    assert f"Warning: skipping, not a directory: {missing}" in err


def test_main_fails_when_no_directories_exist(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert flf.main([str(repo_tmp / "missing")]) == 1

    assert "Error: no directories to scan." in capsys.readouterr().err


def test_main_reports_no_files(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert flf.main([str(repo_tmp)]) == 0

    assert "No files found." in capsys.readouterr().out


def test_main_keeps_progress_off_stdout(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(flf, "PROGRESS_EVERY", 1)
    make_file(repo_tmp / "a.mkv")
    make_file(repo_tmp / "b.mkv")

    flf.main([str(repo_tmp)])

    out, err = capsys.readouterr()
    assert "Scanned" not in out
    assert "Scanned 2 files..." in err


def test_main_reports_non_utf8_filenames(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_file(repo_tmp / BAD_NAME)

    assert flf.main([str(repo_tmp)]) == 0

    out, err = capsys.readouterr()
    assert listed_files(out) == ["bad�.mkv"]
    assert "1 listed file(s) have names that aren't valid UTF-8" in err
    assert "bad\\xda.mkv'" in err


@needs_non_root
def test_main_reports_unreadable_directories(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_file(repo_tmp / "ok.mkv")
    locked = lock(repo_tmp / "locked")
    try:
        assert flf.main([str(repo_tmp)]) == 0
    finally:
        locked.chmod(0o755)

    out, err = capsys.readouterr()
    assert listed_files(out) == ["ok.mkv"]
    assert f"1 path(s) could not be read:\n  {locked}" in err
