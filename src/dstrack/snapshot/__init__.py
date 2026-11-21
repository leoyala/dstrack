"""Snapshot computation for dstrack.

Classes
-------
[SnapshotBuilder][dstrack.snapshot._builder.SnapshotBuilder]
    Builds a complete snapshot from a single reader, combining the two below.

[MetadataBuilder][dstrack.snapshot._metadata.MetadataBuilder]
    Builds identity and structural snapshot fields from a reader's schema.

[StatsComputer][dstrack.snapshot._stats.StatsComputer]
    Computes per-column and dataset-level statistics in a single data pass.

Result types
------------
[SnapshotMetadata][dstrack.snapshot._metadata.SnapshotMetadata],
[DatasetStats][dstrack.snapshot._stats.DatasetStats]

Constants
---------
[DEFAULT_MAX_ROWS][dstrack.snapshot.DEFAULT_MAX_ROWS]
    Default row limit of a [StatsComputer][dstrack.snapshot._stats.StatsComputer].
"""

from ._builder import SnapshotBuilder, build_snapshot_dict
from ._metadata import MetadataBuilder, SnapshotMetadata
from ._stats import (
    DEFAULT_MAX_ROWS,
    DatasetStats,
    DatetimeColumnStats,
    HistogramStats,
    NumericColumnStats,
    OtherColumnStats,
    PercentileStats,
    StatsComputer,
    StringColumnStats,
)

__all__ = [
    "DEFAULT_MAX_ROWS",
    "DatasetStats",
    "DatetimeColumnStats",
    "HistogramStats",
    "MetadataBuilder",
    "NumericColumnStats",
    "OtherColumnStats",
    "PercentileStats",
    "SnapshotBuilder",
    "SnapshotMetadata",
    "StatsComputer",
    "StringColumnStats",
    "build_snapshot_dict",
]
