import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dstrack.readers import ColumnInfo, TabularReader
from dstrack.snapshot._version import FORMAT_VERSION


@dataclass
class SnapshotMetadata:
    """Identity and structural fields for a snapshot.

    Covers every required top-level field that does not require a data pass:
    versioning identifiers, authorship, schema shape, and the source hash.
    """

    format_version: str
    snapshot_id: str
    created_at: str
    created_by: str
    dataset_name: str
    dataset_path: str
    source_type: str
    source_hash: str
    num_columns: int
    columns: list[dict[str, Any]]
    schema_hash: str


class MetadataBuilder:
    """Builds identity and structural metadata without reading data rows.

    Computes snapshot_id, created_at, created_by, dataset_name,
    dataset_path, source_type, source_hash, num_columns, columns, and
    schema_hash from the reader's column descriptors alone.

    Note:
        schema_hash is order-independent: reordering a dataset's columns
        without changing their names or dtypes produces the same hash.
    """

    def build(
        self,
        reader: TabularReader,
        *,
        dataset_name: str,
        dataset_path: str | Path,
        source_type: str,
        created_by: str,
        source: str | Path | None = None,
        source_hash: str | None = None,
    ) -> SnapshotMetadata:
        """Build metadata for a snapshot.

        ``dataset_path`` is what gets *recorded*; ``source`` is what gets
        *read*.  They differ whenever the recorded path is relative to a path
        root that is not the current working directory, as it is for snapshots
        written by the CLI.

        Args:
            reader: Any TabularReader; only ``columns()`` is called.
            dataset_name: Human-readable dataset name stored in the snapshot.
            dataset_path: Source path or URI at snapshot time, recorded in the
                snapshot verbatim.  Never opened.
            source_type: Origin kind (``"file"``, ``"directory"``, etc.).
            created_by: User or process identifier.
            source: Location the data actually lives at, used only to compute
                ``source_hash``.  Defaults to ``dataset_path``, which is
                correct when the recorded path is one the process can open.
            source_hash: Pre-computed source hash.  When ``None`` and the
                source resolves to a regular file, a SHA-256 of the file bytes
                is computed automatically.

        Returns:
            A populated :class:`SnapshotMetadata` instance.
        """
        cols = reader.columns()
        path_str = str(dataset_path)

        if source_hash is None:
            p = Path(source if source is not None else dataset_path)
            source_hash = _hash_file(p) if p.is_file() else ""

        return SnapshotMetadata(
            format_version=FORMAT_VERSION,
            snapshot_id=str(uuid.uuid4()),
            created_at=datetime.now(UTC).isoformat(),
            created_by=created_by,
            dataset_name=dataset_name,
            dataset_path=path_str,
            source_type=source_type,
            source_hash=source_hash,
            num_columns=len(cols),
            columns=_columns_to_dicts(cols),
            schema_hash=_schema_hash(cols),
        )


def _hash_file(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file's contents.

    Args:
        path: Path to the file to hash.

    Returns:
        The hex-encoded SHA-256 digest of the file's bytes.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _columns_to_dicts(cols: list[ColumnInfo]) -> list[dict[str, Any]]:
    """Convert column descriptors into plain dicts for serialization.

    Args:
        cols: Column descriptors as returned by a reader.

    Returns:
        One dict per column with ``name``, ``dtype``, and ``nullable`` keys.
    """
    return [{"name": c.name, "dtype": c.dtype, "nullable": c.nullable} for c in cols]


def _schema_hash(cols: list[ColumnInfo]) -> str:
    """Compute a deterministic hash of a schema's column names and dtypes.

    Args:
        cols: Column descriptors, in any order.

    Returns:
        A hex-encoded SHA-256 digest that changes if any column's name or
        dtype changes. Order-independent: reordering ``cols`` does not
        change the result. Ignores ``nullable``.
    """
    h = hashlib.sha256()
    for name, dtype in sorted((c.name, c.dtype) for c in cols):
        h.update(f"{name}\x00{dtype}\n".encode())
    return h.hexdigest()
