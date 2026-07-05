"""Tests for the `dstrack` CLI in src/dstrack/_cli.py."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from dstrack import _cli
from dstrack._cli import app, init_local_store
from dstrack.errors import StoreInitError

runner = CliRunner()

# ---------------------------------------------------------------------------
# _init_local_store_gitignore
# ---------------------------------------------------------------------------


def test_init_local_store_gitignore_creates_file(tmp_path: Path) -> None:
    """Writes a .gitignore file that ignores the cache directory."""
    _cli._init_local_store_gitignore(tmp_path)

    gitignore = tmp_path / ".gitignore"
    assert gitignore.is_file()
    assert gitignore.read_text() == ".cache/"


def test_init_local_store_gitignore_raises_if_exists(tmp_path: Path) -> None:
    """Refuses to overwrite an already-existing .gitignore file."""
    (tmp_path / ".gitignore").write_text("existing content")

    with pytest.raises(FileExistsError):
        _cli._init_local_store_gitignore(tmp_path)


# ---------------------------------------------------------------------------
# init_local_store
# ---------------------------------------------------------------------------


def test_init_local_store_creates_expected_structure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Creates `.dstrack/`, `.dstrack/datasets/`, and `.dstrack/.gitignore`."""
    monkeypatch.chdir(tmp_path)

    store_path = init_local_store()

    assert store_path == tmp_path / ".dstrack"
    assert store_path.is_dir()
    assert (store_path / "datasets").is_dir()
    assert (store_path / ".gitignore").is_file()


def test_init_local_store_raises_if_store_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails without touching anything if `.dstrack/` already exists."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".dstrack").mkdir()

    with pytest.raises(FileExistsError):
        init_local_store()


def test_init_local_store_rolls_back_on_gitignore_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removes the partially created store and raises StoreInitError.

    if writing the .gitignore file fails partway through.
    """
    monkeypatch.chdir(tmp_path)

    def _boom(path: Path) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(_cli, "_init_local_store_gitignore", _boom)

    with pytest.raises(StoreInitError) as exc_info:
        init_local_store()

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert not (tmp_path / ".dstrack").exists()


# ---------------------------------------------------------------------------
# `init` command
# ---------------------------------------------------------------------------


def test_init_command_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reports success and creates the store on a clean directory."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "Finished creating local store" in result.output
    assert (tmp_path / ".dstrack").is_dir()


def test_init_command_fails_if_store_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exits non-zero and reports an error when the store already exists."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".dstrack").mkdir()

    result = runner.invoke(app, ["init"])

    assert result.exit_code != 0
    assert isinstance(result.exception, FileExistsError)
    assert "already exists" in result.output


def test_init_command_allow_exists_suppresses_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With --allow-exists, an existing store produces a warning, not a failure."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".dstrack").mkdir()

    result = runner.invoke(app, ["init", "--allow-exists"])

    assert result.exit_code == 0
    assert result.exception is None
    assert "already exists" in result.output


def test_init_command_reports_store_init_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Surfaces StoreInitError raised during store creation as a CLI failure."""
    monkeypatch.chdir(tmp_path)

    def _boom() -> Path:
        raise StoreInitError("could not initialize store")

    monkeypatch.setattr(_cli, "init_local_store", _boom)

    result = runner.invoke(app, ["init"])

    assert result.exit_code != 0
    assert isinstance(result.exception, StoreInitError)
    assert "could not initialize store" in result.output


# ---------------------------------------------------------------------------
# `version` command
# ---------------------------------------------------------------------------


def test_version_command_prints_version() -> None:
    """Prints the package version and exits cleanly."""
    from dstrack import __version__

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.output.strip() == __version__
