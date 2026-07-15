import dataclasses
from pathlib import Path, PurePath
from typing import Any

from dstrack.readers import TabularReader
from dstrack.snapshot._metadata import MetadataBuilder, SnapshotMetadata
from dstrack.snapshot._stats import DatasetStats, StatsComputer


class SnapshotBuilder:
    """Builds a complete dataset snapshot from a single reader.

    Combines [MetadataBuilder][dstrack.snapshot._metadata.MetadataBuilder]
    (identity and schema) and
    [StatsComputer][dstrack.snapshot._stats.StatsComputer] (a data pass over
    the rows) into one JSON-ready snapshot dict.  Import it to build snapshots
    from Python without going through the CLI:

    Examples:
        >>> from dstrack.readers import CsvReader
        >>> from dstrack.snapshot import SnapshotBuilder
        >>> reader = CsvReader("data.csv")  # doctest: +SKIP
        >>> snapshot = SnapshotBuilder().build(  # doctest: +SKIP
        ...     reader,
        ...     dataset_name="customers",
        ...     dataset_path="data.csv",
        ...     created_by="alice",
        ... )

    Args:
        metadata_builder: Builder used for identity and schema fields.
            Defaults to a fresh
            [MetadataBuilder][dstrack.snapshot._metadata.MetadataBuilder].
        stats_computer: Computer used for the per-column and dataset-level
            statistics.  Defaults to a fresh
            [StatsComputer][dstrack.snapshot._stats.StatsComputer].  Pass a
            pre-configured ``StatsComputer(max_rows=...)`` here to change the
            in-memory row limit the statistics pass enforces.
    """

    def __init__(
        self,
        *,
        metadata_builder: MetadataBuilder | None = None,
        stats_computer: StatsComputer | None = None,
    ) -> None:
        self._metadata_builder = metadata_builder or MetadataBuilder()
        self._stats_computer = stats_computer or StatsComputer()

    def build(
        self,
        reader: TabularReader,
        *,
        dataset_name: str,
        dataset_path: str | PurePath,
        created_by: str,
        source_type: str = "file",
        source: str | Path | None = None,
        source_hash: str | None = None,
    ) -> dict[str, Any]:
        """Build a JSON-serializable snapshot from a reader.

        The reader is consumed once for schema inference and once for the
        statistics data pass.

        Args:
            reader: Any [TabularReader][dstrack.readers._protocol.TabularReader].
            dataset_name: Human-readable dataset name stored in the snapshot.
            dataset_path: Source path or URI recorded in the snapshot as a
                forward-slash string.  Never opened; pass ``source`` when the
                data lives elsewhere.
            created_by: User or process identifier.
            source_type: Origin kind (``"file"``, ``"directory"``, etc.).
            source: Location the data actually lives at, used only to compute
                ``source_hash``.  Defaults to ``dataset_path``.
            source_hash: Pre-computed source hash.  When ``None`` and the
                source resolves to a regular file, a SHA-256 of the file bytes
                is computed automatically by the metadata builder.

        Returns:
            A ``dict`` merging every metadata and statistics field, ready to be
            serialized with [json.dumps][json.dumps].
        """
        metadata = self._metadata_builder.build(
            reader,
            dataset_name=dataset_name,
            dataset_path=dataset_path,
            source_type=source_type,
            created_by=created_by,
            source=source,
            source_hash=source_hash,
        )
        stats = self._stats_computer.compute(reader)
        return build_snapshot_dict(metadata, stats)


def build_snapshot_dict(
    metadata: SnapshotMetadata, stats: DatasetStats
) -> dict[str, Any]:
    """Merge metadata and statistics into one JSON-serializable snapshot dict.

    The two inputs contribute disjoint sets of keys, so the merge never drops
    a field.

    Args:
        metadata: Identity and schema fields from
            [MetadataBuilder][dstrack.snapshot._metadata.MetadataBuilder].
        stats: Volume, per-column, and quality fields from
            [StatsComputer][dstrack.snapshot._stats.StatsComputer].

    Returns:
        A ``dict`` combining both, with nested dataclasses expanded to dicts.
    """
    return {**dataclasses.asdict(metadata), **dataclasses.asdict(stats)}
