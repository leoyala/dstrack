"""Tests for MetadataBuilder."""

import hashlib
import uuid
from collections.abc import Iterator
from pathlib import Path

from dstrack.readers import ColumnInfo, TabularReader
from dstrack.snapshot import MetadataBuilder, SnapshotMetadata

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


def _make_reader(*cols: ColumnInfo) -> TabularReader:
    class _Stub:
        def columns(self) -> list[ColumnInfo]:
            return list(cols)

        def iter_batches(self, batch_size: int = 1000) -> Iterator[list[list[object]]]:
            return iter([])

    return _Stub()  # type: ignore[return-value]


def _build(
    reader: TabularReader,
    *,
    path: str | Path = "/data/ds.csv",
    source: str | Path | None = None,
    source_hash: str | None = None,
) -> SnapshotMetadata:
    return MetadataBuilder().build(
        reader,
        dataset_name="my_dataset",
        dataset_path=path,
        source_type="file",
        created_by="tester",
        source=source,
        source_hash=source_hash,
    )


# ---------------------------------------------------------------------------
# Basic fields
# ---------------------------------------------------------------------------


def test_format_version_is_one() -> None:
    """Built metadata always reports format_version '1'."""
    meta = _build(_make_reader())
    assert meta.format_version == "1"


def test_snapshot_id_is_valid_uuid() -> None:
    """snapshot_id is a valid UUIDv4 string."""
    meta = _build(_make_reader())
    parsed = uuid.UUID(meta.snapshot_id)
    assert parsed.version == 4


def test_created_at_is_iso8601() -> None:
    """created_at parses as a timezone-aware ISO 8601 timestamp."""
    from datetime import datetime

    meta = _build(_make_reader())
    dt = datetime.fromisoformat(meta.created_at)
    assert dt.tzinfo is not None


def test_created_by_and_name() -> None:
    """created_by, dataset_name, and source_type are passed through as given."""
    meta = _build(_make_reader())
    assert meta.created_by == "tester"
    assert meta.dataset_name == "my_dataset"
    assert meta.source_type == "file"


def test_dataset_path_stored_as_string() -> None:
    """A Path dataset_path is coerced to a string on the built metadata."""
    meta = _build(_make_reader(), path=Path("/some/path/data.csv"))
    assert meta.dataset_path == "/some/path/data.csv"


# ---------------------------------------------------------------------------
# Column descriptors
# ---------------------------------------------------------------------------


def test_num_columns_matches_reader() -> None:
    """num_columns equals the number of columns reported by the reader."""
    cols = [ColumnInfo("a", "int64"), ColumnInfo("b", "string")]
    meta = _build(_make_reader(*cols))
    assert meta.num_columns == 2


def test_columns_serialised_correctly() -> None:
    """Each ColumnInfo is serialised to a name/dtype/nullable dict."""
    cols = [
        ColumnInfo("x", "float64", nullable=False),
        ColumnInfo("y", "string", nullable=True),
    ]
    meta = _build(_make_reader(*cols))
    assert meta.columns == [
        {"name": "x", "dtype": "float64", "nullable": False},
        {"name": "y", "dtype": "string", "nullable": True},
    ]


def test_empty_reader_produces_zero_columns() -> None:
    """A reader with no columns yields num_columns 0 and an empty columns list."""
    meta = _build(_make_reader())
    assert meta.num_columns == 0
    assert meta.columns == []


# ---------------------------------------------------------------------------
# schema_hash
# ---------------------------------------------------------------------------


def test_schema_hash_is_hex_string() -> None:
    """schema_hash is a valid hexadecimal string."""
    meta = _build(_make_reader(ColumnInfo("a", "int64")))
    int(meta.schema_hash, 16)  # raises ValueError if not hex


def test_schema_hash_changes_on_column_rename() -> None:
    """Renaming a column changes the schema_hash."""
    m1 = _build(_make_reader(ColumnInfo("a", "int64")))
    m2 = _build(_make_reader(ColumnInfo("b", "int64")))
    assert m1.schema_hash != m2.schema_hash


def test_schema_hash_changes_on_dtype_change() -> None:
    """Changing a column's dtype changes the schema_hash."""
    m1 = _build(_make_reader(ColumnInfo("a", "int64")))
    m2 = _build(_make_reader(ColumnInfo("a", "float64")))
    assert m1.schema_hash != m2.schema_hash


def test_schema_hash_stable_across_calls() -> None:
    """Building metadata twice from the same reader yields the same schema_hash."""
    reader = _make_reader(ColumnInfo("x", "string"), ColumnInfo("y", "int64"))
    m1 = _build(reader)
    m2 = _build(reader)
    assert m1.schema_hash == m2.schema_hash


def test_schema_hash_order_independent() -> None:
    """schema_hash is unaffected by the order columns are declared in."""
    m1 = _build(_make_reader(ColumnInfo("a", "int64"), ColumnInfo("b", "string")))
    m2 = _build(_make_reader(ColumnInfo("b", "string"), ColumnInfo("a", "int64")))
    assert m1.schema_hash == m2.schema_hash


# ---------------------------------------------------------------------------
# source_hash
# ---------------------------------------------------------------------------


def test_explicit_source_hash_used_verbatim() -> None:
    """An explicitly provided source_hash is stored as-is, not recomputed."""
    meta = _build(_make_reader(), source_hash="abc123")
    assert meta.source_hash == "abc123"


def test_source_hash_computed_from_file(tmp_path: Path) -> None:
    """Without an explicit hash, source_hash is the SHA-256 of the file contents."""
    content = b"col\n1\n2\n"
    p = tmp_path / "ds.csv"
    p.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()

    meta = _build(_make_reader(), path=p)
    assert meta.source_hash == expected


def test_source_hash_empty_for_nonexistent_path() -> None:
    """source_hash is an empty string when the dataset path doesn't exist."""
    meta = _build(_make_reader(), path="/nonexistent/path.csv")
    assert meta.source_hash == ""


def test_source_hashed_while_dataset_path_recorded(tmp_path: Path) -> None:
    """The source is hashed; the unopenable dataset_path is recorded verbatim."""
    content = b"col\n1\n2\n"
    p = tmp_path / "ds.csv"
    p.write_bytes(content)

    meta = _build(_make_reader(), path="data/ds.csv", source=p)

    assert meta.dataset_path == "data/ds.csv"
    assert meta.source_hash == hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# Each build produces a unique snapshot_id
# ---------------------------------------------------------------------------


def test_snapshot_ids_differ_across_builds() -> None:
    """Repeated builds from the same reader each get a unique snapshot_id."""
    reader = _make_reader()
    ids = {_build(reader).snapshot_id for _ in range(5)}
    assert len(ids) == 5
