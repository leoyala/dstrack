"""Tests for write_snapshot and lineage resolution."""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from dstrack.errors import DatasetNotFoundError
from dstrack.store import write_snapshot


def _snapshot(dataset_path: str = "data/ds.csv", **overrides: Any) -> dict[str, Any]:
    snapshot = {
        "snapshot_id": str(uuid.uuid4()),
        "dataset_path": dataset_path,
        "dataset_name": "ds",
        "num_rows": 3,
        "num_columns": 2,
    }
    snapshot.update(overrides)
    return snapshot


def test_first_snapshot_mints_a_dataset(tmp_path: Path) -> None:
    """A path no dataset has recorded starts a new lineage with no parent."""
    result = write_snapshot(_snapshot(), store_root=tmp_path)

    assert result.is_new_dataset
    assert result.parent_snapshot_id is None
    assert result.snapshot_path.is_file()


def test_same_path_continues_the_lineage(tmp_path: Path) -> None:
    """Re-tracking the same path joins the existing dataset, parented on HEAD."""
    first = write_snapshot(_snapshot(), store_root=tmp_path)
    second = write_snapshot(_snapshot(), store_root=tmp_path)

    assert not second.is_new_dataset
    assert second.dataset_id == first.dataset_id
    assert second.parent_snapshot_id == first.snapshot_id


def test_different_path_mints_a_separate_dataset(tmp_path: Path) -> None:
    """An unmatched path is a new dataset, not a continuation of another."""
    first = write_snapshot(_snapshot("data/a.csv"), store_root=tmp_path)
    second = write_snapshot(_snapshot("data/b.csv"), store_root=tmp_path)

    assert second.is_new_dataset
    assert second.dataset_id != first.dataset_id


def test_explicit_dataset_id_continues_that_lineage(tmp_path: Path) -> None:
    """An explicit id continues its dataset even when the path has changed."""
    first = write_snapshot(_snapshot("data/old.csv"), store_root=tmp_path)
    moved = write_snapshot(
        _snapshot("data/new.csv"), store_root=tmp_path, dataset_id=first.dataset_id
    )

    assert not moved.is_new_dataset
    assert moved.dataset_id == first.dataset_id
    assert moved.parent_snapshot_id == first.snapshot_id


def test_unknown_dataset_id_is_rejected(tmp_path: Path) -> None:
    """An id naming no dataset fails instead of silently minting one."""
    write_snapshot(_snapshot(), store_root=tmp_path)

    with pytest.raises(DatasetNotFoundError):
        write_snapshot(_snapshot(), store_root=tmp_path, dataset_id="not-a-real-id")


def test_rejected_dataset_id_writes_nothing(tmp_path: Path) -> None:
    """A rejected id leaves no orphan dataset directory behind."""
    first = write_snapshot(_snapshot(), store_root=tmp_path)

    with pytest.raises(DatasetNotFoundError):
        write_snapshot(_snapshot(), store_root=tmp_path, dataset_id="not-a-real-id")

    datasets = sorted(p.name for p in (tmp_path / "datasets").iterdir())
    assert datasets == [first.dataset_id]


def test_log_line_appended_per_snapshot(tmp_path: Path) -> None:
    """Every snapshot appends exactly one identity line to the dataset's log."""
    first = write_snapshot(_snapshot(), store_root=tmp_path)
    second = write_snapshot(_snapshot(), store_root=tmp_path)

    log_path = tmp_path / "datasets" / first.dataset_id / "log.jsonl"
    entries = [json.loads(line) for line in log_path.read_text().splitlines()]

    assert [e["snapshot_id"] for e in entries] == [
        first.snapshot_id,
        second.snapshot_id,
    ]
    assert (tmp_path / "datasets" / first.dataset_id / "HEAD").read_text().strip() == (
        second.snapshot_id
    )


def test_trailing_log_entry_past_head_is_ignored(tmp_path: Path) -> None:
    """Path matching follows committed HEAD, not a log line a crash left behind.

    A crash between the ``log.jsonl`` append and the ``HEAD`` write leaves the
    log one entry ahead of what is committed. Re-tracking the path must continue
    the lineage from HEAD and ignore that uncommitted trailing line.
    """
    first = write_snapshot(_snapshot("data/ds.csv"), store_root=tmp_path)
    dataset_dir = tmp_path / "datasets" / first.dataset_id

    # Simulate the crash: a log line for a snapshot HEAD never advanced onto,
    # recording a different path than the committed HEAD.
    phantom = {"snapshot_id": "phantom", "dataset_path": "data/other.csv"}
    with (dataset_dir / "log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(phantom) + "\n")

    second = write_snapshot(_snapshot("data/ds.csv"), store_root=tmp_path)

    assert not second.is_new_dataset
    assert second.dataset_id == first.dataset_id
    assert second.parent_snapshot_id == first.snapshot_id


@pytest.mark.parametrize("bad_id", ["../../evil", "a/b", "/etc/passwd"])
def test_traversal_snapshot_id_is_rejected(tmp_path: Path, bad_id: str) -> None:
    """A snapshot_id that would escape the dataset's snapshots/ is refused."""
    with pytest.raises(ValueError):
        write_snapshot(_snapshot(snapshot_id=bad_id), store_root=tmp_path)


@pytest.mark.parametrize("bad_id", ["../../evil", "a/b", "/etc/passwd"])
def test_traversal_dataset_id_is_rejected(tmp_path: Path, bad_id: str) -> None:
    """An explicit dataset_id that would escape datasets/ is refused."""
    with pytest.raises(ValueError):
        write_snapshot(_snapshot(), store_root=tmp_path, dataset_id=bad_id)


def test_rejected_snapshot_id_writes_nothing(tmp_path: Path) -> None:
    """A rejected snapshot_id leaves no dataset directory behind."""
    with pytest.raises(ValueError):
        write_snapshot(_snapshot(snapshot_id="../../evil"), store_root=tmp_path)

    assert not (tmp_path / "datasets").exists()
