import os
from pathlib import Path
from typing import List

import pytest

from find_largest_files import find_largest_files, main


def test_find_largest_files_static_data() -> None:
    data_dir = Path(__file__).parent / "data" / "sample"
    results = find_largest_files(data_dir)
    names = [path.name for _, path in results]
    assert names[0] == "file2.txt"
    assert "file1.txt" in names and "file3.txt" in names


def test_find_largest_files_returns_sorted(tmp_path: Path) -> None:
    file_a = tmp_path / "a.txt"
    file_a.write_text("a" * 1)
    file_b = tmp_path / "b.txt"
    file_b.write_text("b" * 2)
    file_c = tmp_path / "c.txt"
    file_c.write_text("c" * 3)

    results = find_largest_files(tmp_path)
    names: List[str] = [path.name for _, path in results]
    assert names == ["c.txt", "b.txt", "a.txt"]


def test_main_with_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "big").write_text("x" * 5)
    (tmp_path / "small").write_text("x")

    exit_code = main([str(tmp_path)])
    captured = capsys.readouterr().out.strip().splitlines()

    assert exit_code == 0
    assert captured[0].endswith("big")
    assert captured[1].endswith("small")


def test_main_with_invalid_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("not a directory")

    exit_code = main([str(file_path)])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "not a directory" in output
