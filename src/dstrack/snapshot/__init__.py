"""Snapshot computation for dstrack.

Classes
-------
:class:`MetadataBuilder`
    Builds identity and structural snapshot fields from a reader's schema.

:class:`StatsComputer`
    Computes per-column and dataset-level statistics in a single data pass.

Result types
------------
:class:`SnapshotMetadata`, :class:`DatasetStats`
"""

from ._builder import SnapshotBuilder, build_snapshot_dict
from ._metadata import MetadataBuilder, SnapshotMetadata
from ._stats import (
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
