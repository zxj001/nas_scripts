import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

import pytest

import defrag
from tests.helpers import BAD_NAME, make_file, write_script


def real_tool(name: str) -> Optional[str]:
    path = shutil.which(name) or f"/usr/sbin/{name}"
    return path if os.path.isfile(path) else None


FILEFRAG = real_tool("filefrag")
E4DEFRAG = real_tool("e4defrag")

needs_filefrag = pytest.mark.skipif(FILEFRAG is None, reason="filefrag not installed")
needs_e4defrag = pytest.mark.skipif(E4DEFRAG is None, reason="e4defrag not installed")


@pytest.fixture
def fake_extents(monkeypatch: pytest.MonkeyPatch) -> Dict[str, int]:
    """Report extent counts from a {filename: extents} dict instead of filefrag."""
    extents: Dict[str, int] = {}
    monkeypatch.setattr(defrag, "find_tool", lambda name: name)
    monkeypatch.setattr(
        defrag,
        "get_extents",
        lambda filefrag, filename: extents.get(os.path.basename(filename)),
    )
    return extents


def item(path: Path, extents: int, size: int = 1024**3) -> dict:
    return {
        "path": str(path),
        "size": size,
        "extents": extents,
        "avg_extent": size / extents,
    }


def listed_files(output: str) -> List[str]:
    """Filenames from the results table, in the order printed."""
    lines = output.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("-" * 8)) + 1
    names = []
    for line in lines[start:]:
        if not line.strip():
            break
        names.append(line.rsplit("/", 1)[-1])
    return names


# find_tool


def test_find_tool_uses_path(repo_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = write_script(repo_tmp / "sometool", "exit 0\n")
    monkeypatch.setenv("PATH", str(repo_tmp))
    assert defrag.find_tool("sometool") == tool


@pytest.mark.skipif(
    not os.path.isfile("/usr/sbin/filefrag"), reason="no /usr/sbin/filefrag"
)
def test_find_tool_falls_back_to_usr_sbin(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(repo_tmp))
    assert defrag.find_tool("filefrag") == "/usr/sbin/filefrag"


def test_find_tool_exits_when_missing(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PATH", str(repo_tmp))
    with pytest.raises(SystemExit) as exc:
        defrag.find_tool("no-such-defrag-tool")
    assert exc.value.code == 1
    assert "no-such-defrag-tool not found" in capsys.readouterr().err


# get_extents


@pytest.mark.parametrize(
    "output, expected",
    [
        ("movie.mkv: 82 extents found", 82),
        ("movie.mkv: 1 extent found", 1),
        ("movie.mkv: FIBMAP/FIEMAP unsupported", None),
        ("", None),
    ],
)
def test_get_extents_parses_filefrag_output(
    repo_tmp: Path, output: str, expected: Optional[int]
) -> None:
    filefrag = write_script(repo_tmp / "filefrag", f"echo '{output}'\n")
    assert defrag.get_extents(filefrag, "movie.mkv") == expected


def test_get_extents_tolerates_non_utf8_output(repo_tmp: Path) -> None:
    # filefrag echoes the filename back, which used to crash the scan.
    filefrag = write_script(
        repo_tmp / "filefrag", "printf 'bad\\332.mkv: 3 extents found\\n'\n"
    )
    assert defrag.get_extents(filefrag, BAD_NAME) == 3


def test_get_extents_returns_none_when_filefrag_missing(repo_tmp: Path) -> None:
    assert defrag.get_extents(str(repo_tmp / "missing"), "movie.mkv") is None


@needs_filefrag
def test_get_extents_with_real_filefrag(repo_tmp: Path) -> None:
    target = repo_tmp / "movie.mkv"
    target.write_bytes(os.urandom(8192))
    assert defrag.get_extents(FILEFRAG, str(target)) == 1


# scan_directory


def test_scan_directory_collects_nested_files(repo_tmp: Path) -> None:
    filefrag = write_script(
        repo_tmp / "bin" / "filefrag",
        'case "$1" in\n'
        '    *.unknown) echo "$1: FIEMAP not supported" ;;\n'
        '    *) echo "$1: 4 extents found" ;;\n'
        "esac\n",
    )
    media = repo_tmp / "media"
    make_file(media / "a.mkv", 8000)
    make_file(media / "sub" / "b.mkv", 4000)
    make_file(media / "empty.mkv", 0)
    make_file(media / "c.unknown", 100)

    results = defrag.scan_directory(str(media), filefrag)

    found = {
        os.path.basename(r["path"]): (r["size"], r["extents"], r["avg_extent"])
        for r in results
    }
    assert found == {"a.mkv": (8000, 4, 2000), "b.mkv": (4000, 4, 1000)}


@needs_filefrag
def test_scan_directory_handles_non_utf8_filenames(repo_tmp: Path) -> None:
    (repo_tmp / BAD_NAME).write_bytes(os.urandom(4096))

    results = defrag.scan_directory(str(repo_tmp), FILEFRAG)

    assert [os.path.basename(r["path"]) for r in results] == [BAD_NAME]
    assert results[0]["extents"] == 1


# main


def test_main_scans_main_directories_by_default(
    repo_tmp: Path,
    fake_extents: Dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    drive1, drive2 = repo_tmp / "Drive1", repo_tmp / "Drive2"
    make_file(drive1 / "a.mkv")
    make_file(drive2 / "b.mkv")
    fake_extents.update({"a.mkv": 2, "b.mkv": 5})
    monkeypatch.setattr(defrag, "MAIN_DIRECTORIES", [str(drive1), str(drive2)])

    defrag.main([])

    out, err = capsys.readouterr()
    assert listed_files(out) == ["b.mkv", "a.mkv"]
    assert f"Scanning: {drive1}" in err
    assert f"Scanning: {drive2}" in err


def test_main_directory_argument_overrides_main_directories(
    repo_tmp: Path,
    fake_extents: Dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_file(repo_tmp / "default" / "a.mkv")
    make_file(repo_tmp / "chosen" / "b.mkv")
    fake_extents.update({"a.mkv": 2, "b.mkv": 5})
    monkeypatch.setattr(defrag, "MAIN_DIRECTORIES", [str(repo_tmp / "default")])

    defrag.main([str(repo_tmp / "chosen")])

    assert listed_files(capsys.readouterr().out) == ["b.mkv"]


def test_main_lists_most_fragmented_first(
    repo_tmp: Path,
    fake_extents: Dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    drive1, drive2 = repo_tmp / "Drive1", repo_tmp / "Drive2"
    make_file(drive1 / "low.mkv")
    make_file(drive1 / "tie_big.mkv", 8 * 1024**2)
    make_file(drive2 / "high.mkv")
    make_file(drive2 / "tie_small.mkv", 1024**2)
    fake_extents.update(
        {"low.mkv": 3, "tie_big.mkv": 10, "high.mkv": 50, "tie_small.mkv": 10}
    )
    monkeypatch.setattr(defrag, "MAIN_DIRECTORIES", [str(drive1), str(drive2)])

    defrag.main([])

    # Ties on extents go to the file with the smaller average extent.
    assert listed_files(capsys.readouterr().out) == [
        "high.mkv",
        "tie_small.mkv",
        "tie_big.mkv",
        "low.mkv",
    ]


def test_main_top_keeps_most_fragmented(
    repo_tmp: Path, fake_extents: Dict[str, int], capsys: pytest.CaptureFixture[str]
) -> None:
    for name, extents in {"a.mkv": 5, "b.mkv": 50, "c.mkv": 20}.items():
        make_file(repo_tmp / name)
        fake_extents[name] = extents

    defrag.main([str(repo_tmp), "-n", "2"])

    assert listed_files(capsys.readouterr().out) == ["b.mkv", "c.mkv"]


def test_main_min_size_filters_small_files(
    repo_tmp: Path, fake_extents: Dict[str, int], capsys: pytest.CaptureFixture[str]
) -> None:
    make_file(repo_tmp / "big.mkv", 2 * 1024**3)
    make_file(repo_tmp / "small.mkv", 1024)
    fake_extents.update({"big.mkv": 2, "small.mkv": 100})

    defrag.main([str(repo_tmp), "--min-size", "1"])

    assert listed_files(capsys.readouterr().out) == ["big.mkv"]


def test_main_skips_missing_directories(
    repo_tmp: Path,
    fake_extents: Dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_file(repo_tmp / "Drive1" / "a.mkv")
    fake_extents["a.mkv"] = 2
    missing = repo_tmp / "Unplugged"
    monkeypatch.setattr(
        defrag, "MAIN_DIRECTORIES", [str(repo_tmp / "Drive1"), str(missing)]
    )

    defrag.main([])

    out, err = capsys.readouterr()
    assert listed_files(out) == ["a.mkv"]
    assert f"Warning: skipping, not a directory: {missing}" in err


def test_main_exits_when_no_directories_exist(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        defrag.main([str(repo_tmp / "missing")])

    assert exc.value.code == 1
    assert "no directories to scan" in capsys.readouterr().err


def test_main_reports_no_files(
    repo_tmp: Path, fake_extents: Dict[str, int], capsys: pytest.CaptureFixture[str]
) -> None:
    defrag.main([str(repo_tmp)])

    assert "No files found." in capsys.readouterr().out


@needs_filefrag
def test_main_prints_non_utf8_filenames(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo_tmp / BAD_NAME).write_bytes(os.urandom(4096))

    defrag.main([str(repo_tmp)])

    assert listed_files(capsys.readouterr().out) == ["bad�.mkv"]


@pytest.mark.parametrize("flag, expected", [([], []), (["--defrag"], [["b.mkv", "c.mkv"]])])
def test_main_defrag_flag_defrags_listed_files(
    repo_tmp: Path,
    fake_extents: Dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
    flag: List[str],
    expected: List[List[str]],
) -> None:
    calls = []
    monkeypatch.setattr(
        defrag, "defrag_files", lambda results, filefrag: calls.append(results)
    )
    for name, extents in {"a.mkv": 5, "b.mkv": 50, "c.mkv": 20}.items():
        make_file(repo_tmp / name)
        fake_extents[name] = extents

    defrag.main([str(repo_tmp), "-n", "2", *flag])

    assert [[os.path.basename(i["path"]) for i in r] for r in calls] == expected


# defrag_files


def install_fake_e4defrag(
    monkeypatch: pytest.MonkeyPatch, directory: Path, exit_code: int = 0
) -> Path:
    """Replace e4defrag with a script that logs each file it's given."""
    log = directory / "e4defrag.log"
    script = write_script(
        directory / "bin" / "e4defrag",
        f'echo "$1" >> "{log}"\n'
        'echo "e4defrag: fake output for $1"\n'
        f"exit {exit_code}\n",
    )
    monkeypatch.setattr(defrag, "find_tool", lambda name: script)
    return log


def set_extents_after(
    monkeypatch: pytest.MonkeyPatch, after: Dict[str, Optional[int]]
) -> None:
    monkeypatch.setattr(
        defrag, "get_extents", lambda filefrag, filename: after.get(filename)
    )


def test_defrag_files_reports_extents_before_and_after(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    log = install_fake_e4defrag(monkeypatch, repo_tmp)
    target = repo_tmp / "movie.mkv"
    set_extents_after(monkeypatch, {str(target): 1})

    defrag.defrag_files([item(target, 120)], "filefrag")

    out, err = capsys.readouterr()
    assert "extents: 120 -> 1" in out
    assert "no improvement" not in out
    assert err == ""
    assert log.read_text().splitlines() == [str(target)]


def test_defrag_files_skips_single_extent_files(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    log = install_fake_e4defrag(monkeypatch, repo_tmp)
    done, fragmented = repo_tmp / "done.mkv", repo_tmp / "fragmented.mkv"
    set_extents_after(monkeypatch, {str(fragmented): 1})

    defrag.defrag_files([item(fragmented, 7), item(done, 1)], "filefrag")

    assert "Defragmenting 1 file(s)" in capsys.readouterr().out
    assert log.read_text().splitlines() == [str(fragmented)]


def test_defrag_files_does_nothing_when_nothing_is_fragmented(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    log = install_fake_e4defrag(monkeypatch, repo_tmp)

    defrag.defrag_files([item(repo_tmp / "done.mkv", 1)], "filefrag")

    assert "Nothing to defragment." in capsys.readouterr().out
    assert not log.exists()


def test_defrag_files_explains_when_nothing_improved(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install_fake_e4defrag(monkeypatch, repo_tmp)
    target = repo_tmp / "movie.mkv"
    set_extents_after(monkeypatch, {str(target): 40})

    defrag.defrag_files([item(target, 40)], "filefrag")

    out = capsys.readouterr().out
    assert "extents: 40 -> 40" in out
    assert "no improvement" in out


def test_defrag_files_reports_e4defrag_failures(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install_fake_e4defrag(monkeypatch, repo_tmp, exit_code=1)
    target = repo_tmp / "movie.mkv"
    set_extents_after(monkeypatch, {str(target): 40})

    defrag.defrag_files([item(target, 40)], "filefrag")

    out, err = capsys.readouterr()
    assert "extents:" not in out
    assert f"fake output for {target}" in err
    assert "1 file(s) failed to defragment." in err


def test_defrag_files_counts_vanished_file_as_failure(
    repo_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install_fake_e4defrag(monkeypatch, repo_tmp)
    set_extents_after(monkeypatch, {})

    defrag.defrag_files([item(repo_tmp / "gone.mkv", 40)], "filefrag")

    assert "1 file(s) failed to defragment." in capsys.readouterr().err


def make_fragmented_file(directory: Path) -> Path:
    """Interleave small synced writes to two files so their blocks alternate."""
    target = directory / "fragmented.bin"
    chunk = os.urandom(4096)
    with open(target, "wb") as a, open(directory / "interleave.bin", "wb") as b:
        for _ in range(16):
            for f in (a, b):
                f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
    return target


@needs_filefrag
@needs_e4defrag
def test_defrag_files_with_real_e4defrag(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = make_fragmented_file(repo_tmp)
    before = defrag.get_extents(FILEFRAG, str(target))
    if before is None or before < 2:
        pytest.skip("could not create a fragmented file on this filesystem")
    content = target.read_bytes()

    defrag.defrag_files([item(target, before, target.stat().st_size)], FILEFRAG)

    assert defrag.get_extents(FILEFRAG, str(target)) < before
    assert target.read_bytes() == content
