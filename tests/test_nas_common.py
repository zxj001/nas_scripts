from pathlib import Path

import pytest

from nas_common import display_path, existing_directories, human_size
from tests.helpers import BAD_NAME, make_file


@pytest.mark.parametrize(
    "num_bytes, expected",
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.0 KiB"),
        (1536, "1.5 KiB"),
        (5 * 1024**3, "5.0 GiB"),
        (2048 * 1024**4, "2048.0 TiB"),
    ],
)
def test_human_size(num_bytes: int, expected: str) -> None:
    assert human_size(num_bytes) == expected


def test_display_path_leaves_utf8_paths_alone() -> None:
    assert display_path("/media/Anime/進撃の巨人.mkv") == "/media/Anime/進撃の巨人.mkv"


def test_display_path_replaces_invalid_bytes() -> None:
    shown = display_path("/media/Anime/" + BAD_NAME)
    assert shown == "/media/Anime/bad�.mkv"
    shown.encode("utf-8")  # must be printable


def test_existing_directories_skips_missing_ones(
    repo_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    present = repo_tmp / "Drive1"
    present.mkdir()
    missing = repo_tmp / "Drive2"
    not_a_dir = make_file(repo_tmp / "movie.mkv")

    found = existing_directories([str(present), str(missing), str(not_a_dir)])

    assert found == [str(present)]
    err = capsys.readouterr().err
    assert f"Warning: skipping, not a directory: {missing}" in err
    assert f"Warning: skipping, not a directory: {not_a_dir}" in err
