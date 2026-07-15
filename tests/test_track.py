"""Tests for the `dstrack track` command in src/dstrack/_track.py."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dstrack import _track
from dstrack._cli import app
from dstrack._track import _default_created_by

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an empty local store under a fresh cwd and return its root.

    The `track` command resolves the store by walking up from the current
    directory, so the tests run from inside `tmp_path`. `DSTRACK_ROOT_PATH`
    is cleared so an ambient value cannot redirect the store elsewhere.
    """
    monkeypatch.delenv("DSTRACK_ROOT_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    store_root = tmp_path / ".dstrack"
    (store_root / "datasets").mkdir(parents=True)
    return store_root


def _write_csv(path: Path, rows: int = 3) -> Path:
    """Write a small, valid CSV file and return its path."""
    lines = ["a,b"] + [f"{i},{i * 2}" for i in range(rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _load_only_snapshot(store_root: Path) -> dict[str, Any]:
    """Load the single snapshot JSON written under the store."""
    snapshots = list(store_root.glob("datasets/*/snapshots/*.json"))
    assert len(snapshots) == 1, f"expected exactly one snapshot, got {snapshots}"
    return json.loads(snapshots[0].read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_track_creates_new_dataset_snapshot(tmp_path: Path, store: Path) -> None:
    """Tracking a fresh file writes a snapshot and reports a new dataset."""
    csv = _write_csv(tmp_path / "data.csv")

    result = runner.invoke(app, ["track", str(csv)])

    assert result.exit_code == 0, result.output
    assert "new dataset" in result.output
    assert "written" in result.output
    snapshot = _load_only_snapshot(store)
    assert snapshot["num_rows"] == 3
    assert snapshot["parent_snapshot_id"] is None


def test_track_same_file_twice_continues_lineage(tmp_path: Path, store: Path) -> None:
    """Re-tracking the same path extends the lineage rather than starting one."""
    csv = _write_csv(tmp_path / "data.csv")

    first = runner.invoke(app, ["track", str(csv)])
    second = runner.invoke(app, ["track", str(csv)])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "new dataset" in first.output
    assert "continued lineage" in second.output
    # Both snapshots live under the same dataset directory.
    dataset_dirs = list((store / "datasets").iterdir())
    assert len(dataset_dirs) == 1
    # Snapshot dir should contain 2 snapshots + HEAD file
    snapshot_dir = dataset_dirs[0]
    assert len(list(snapshot_dir.iterdir())) == 3


def test_track_default_dataset_name_is_file_stem(tmp_path: Path, store: Path) -> None:
    """Without --name the dataset name defaults to the file's stem."""
    csv = _write_csv(tmp_path / "customers.csv")

    result = runner.invoke(app, ["track", str(csv)])

    assert result.exit_code == 0, result.output
    assert _load_only_snapshot(store)["dataset_name"] == "customers"


def test_track_name_option_overrides_stem(tmp_path: Path, store: Path) -> None:
    """--name overrides the default dataset name derived from the file stem."""
    csv = _write_csv(tmp_path / "customers.csv")

    result = runner.invoke(app, ["track", str(csv), "--name", "clients"])

    assert result.exit_code == 0, result.output
    assert _load_only_snapshot(store)["dataset_name"] == "clients"


def test_track_created_by_option_is_recorded(tmp_path: Path, store: Path) -> None:
    """--created-by overrides the recorded author."""
    csv = _write_csv(tmp_path / "data.csv")

    result = runner.invoke(app, ["track", str(csv), "--created-by", "alice"])

    assert result.exit_code == 0, result.output
    assert _load_only_snapshot(store)["created_by"] == "alice"


def test_track_records_path_relative_to_store_root(tmp_path: Path, store: Path) -> None:
    """The dataset_path is stored relative to the store's parent by default."""
    sub = tmp_path / "sub"
    sub.mkdir()
    csv = _write_csv(sub / "data.csv")

    result = runner.invoke(app, ["track", str(csv)])

    assert result.exit_code == 0, result.output
    assert _load_only_snapshot(store)["dataset_path"] == "sub/data.csv"


def test_track_root_option_changes_recorded_path(tmp_path: Path, store: Path) -> None:
    """--root controls what the recorded dataset_path is made relative to."""
    sub = tmp_path / "sub"
    sub.mkdir()
    csv = _write_csv(sub / "data.csv")

    result = runner.invoke(app, ["track", str(csv), "--root", str(sub)])

    assert result.exit_code == 0, result.output
    assert _load_only_snapshot(store)["dataset_path"] == "data.csv"


def test_track_explicit_reader_reads_mislabeled_file(
    tmp_path: Path, store: Path
) -> None:
    """--reader wins over extension inference, so a .data file reads as CSV."""
    csv = _write_csv(tmp_path / "data.unknownext")

    result = runner.invoke(app, ["track", str(csv), "--reader", "csv"])

    assert result.exit_code == 0, result.output
    assert _load_only_snapshot(store)["num_rows"] == 3


# ---------------------------------------------------------------------------
# `--dataset-id`
# ---------------------------------------------------------------------------


def test_track_dataset_id_continues_that_lineage(tmp_path: Path, store: Path) -> None:
    """--dataset-id attaches the snapshot to an existing lineage after a move."""
    first_csv = _write_csv(tmp_path / "old.csv")
    first = runner.invoke(app, ["track", str(first_csv)])
    assert first.exit_code == 0, first.output
    dataset_id = next((store / "datasets").iterdir()).name

    moved_csv = _write_csv(tmp_path / "new.csv")
    second = runner.invoke(app, ["track", str(moved_csv), "--dataset-id", dataset_id])

    assert second.exit_code == 0, second.output
    assert "continued lineage" in second.output
    assert len(list((store / "datasets").iterdir())) == 1


def test_track_unknown_dataset_id_errors(tmp_path: Path, store: Path) -> None:
    """An unknown --dataset-id fails cleanly instead of minting a lineage."""
    csv = _write_csv(tmp_path / "data.csv")

    result = runner.invoke(app, ["track", str(csv), "--dataset-id", "does-not-exist"])

    assert result.exit_code == 1
    assert "does-not-exist" in result.output


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_track_without_store_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no `.dstrack/` anywhere above, the command exits non-zero."""
    monkeypatch.delenv("DSTRACK_ROOT_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    csv = _write_csv(tmp_path / "data.csv")

    result = runner.invoke(app, ["track", str(csv)])

    assert result.exit_code == 1
    assert ".dstrack" in result.output


def test_track_unknown_extension_hints_at_reader(tmp_path: Path, store: Path) -> None:
    """An unresolvable extension fails and suggests naming a reader explicitly."""
    data = _write_csv(tmp_path / "data.unknownext")

    result = runner.invoke(app, ["track", str(data)])

    assert result.exit_code == 1
    assert "--reader" in result.output


def test_track_unknown_named_reader_errors_without_hint(
    tmp_path: Path, store: Path
) -> None:
    """A bad explicit --reader fails without appending the --reader hint."""
    csv = _write_csv(tmp_path / "data.csv")

    result = runner.invoke(app, ["track", str(csv), "--reader", "nope"])

    assert result.exit_code == 1
    assert "Unknown reader" in result.output
    assert "Use --reader to name one explicitly." not in result.output


def test_track_missing_path_is_rejected_by_typer(tmp_path: Path, store: Path) -> None:
    """A path that does not exist is rejected by argument validation."""
    result = runner.invoke(app, ["track", str(tmp_path / "missing.csv")])

    assert result.exit_code != 0
    assert result.exit_code != 1  # a usage error, not our handled Exit(1)


def test_track_directory_path_is_rejected_by_typer(tmp_path: Path, store: Path) -> None:
    """A directory is rejected: the argument is declared dir_okay=False."""
    result = runner.invoke(app, ["track", str(tmp_path)])

    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# _default_created_by
# ---------------------------------------------------------------------------


def test_default_created_by_returns_username(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns the current username when getpass can determine one."""
    monkeypatch.setattr(_track.getpass, "getuser", lambda: "bob")

    assert _default_created_by() == "bob"


def test_default_created_by_falls_back_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falls back to 'unknown' when the username cannot be determined."""

    def _boom() -> str:
        raise OSError("no username")

    monkeypatch.setattr(_track.getpass, "getuser", _boom)

    assert _default_created_by() == "unknown"
