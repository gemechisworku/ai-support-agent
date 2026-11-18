"""VOICE-R4: the end-of-turn model's files are checked in the Hugging Face cache, not trusted from a marker.

The fake cache mirrors the layout `huggingface_hub` reads: `models--<org>--<name>/refs/<revision>` holds the
commit hash and `snapshots/<hash>/<file>` the files. No network and no model download.
"""

from pathlib import Path

import pytest

from supportagent.voice import providers, turn_files


def _fake_cache(root: Path, files: tuple[str, ...]) -> Path:
    repo, revision = turn_files._repo_and_revision()
    base = root / f"models--{repo.replace('/', '--')}"
    (base / "refs").mkdir(parents=True)
    (base / "refs" / revision).write_text("abc123")
    for name in files:
        path = base / "snapshots" / "abc123" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    return root


def test_an_empty_cache_is_missing_everything(tmp_path: Path) -> None:
    assert turn_files.missing_files(tmp_path) == list(turn_files.FILES)
    assert turn_files.files_present(tmp_path) is False


def test_a_complete_cache_is_present(tmp_path: Path) -> None:
    cache = _fake_cache(tmp_path, turn_files.FILES)
    assert turn_files.missing_files(cache) == []
    assert turn_files.files_present(cache) is True


def test_one_missing_file_is_reported_by_name(tmp_path: Path) -> None:
    cache = _fake_cache(tmp_path, ("languages.json", "tokenizer.json"))  # no ONNX weights
    assert turn_files.missing_files(cache) == ["onnx/model_q8.onnx"]


def test_main_succeeds_without_downloading_when_the_files_are_there(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(turn_files, "files_present", lambda cache_dir=None: True)
    monkeypatch.setattr(turn_files, "download", lambda: pytest.fail("must not download"))
    assert turn_files.main() == 0
    assert "already in the cache" in capsys.readouterr().out


def test_main_fails_when_the_download_leaves_files_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A downloader that returns without fetching (tonight's bug) must not count as success."""
    monkeypatch.setattr(turn_files, "download", lambda: None)
    monkeypatch.setattr(turn_files, "missing_files", lambda cache_dir=None: ["languages.json"])
    monkeypatch.setattr(turn_files, "files_present", lambda cache_dir=None: False)
    assert turn_files.main() == 1
    assert "still missing" in capsys.readouterr().err


def test_main_reports_a_failed_download(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom() -> None:
        raise OSError("no network")

    monkeypatch.setattr(turn_files, "files_present", lambda cache_dir=None: False)
    monkeypatch.setattr(turn_files, "download", boom)
    assert turn_files.main() == 1
    assert "OSError: no network" in capsys.readouterr().err


def test_building_the_turn_detector_without_its_files_says_what_to_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(turn_files, "missing_files", lambda cache_dir=None: ["onnx/model_q8.onnx"])
    with pytest.raises(RuntimeError, match=r"python -m supportagent\.voice\.turn_files"):
        providers.build_turn_detector()
