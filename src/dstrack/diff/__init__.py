"""Comparison of two dataset snapshots.

Takes two snapshot payloads -- the plain dicts
[SnapshotBuilder][dstrack.snapshot.SnapshotBuilder] produces and
[write_snapshot][dstrack.store.write_snapshot] stores -- and reports what
changed between them: schema, dataset-level metrics, and per-column
statistics, each classified by how much it should worry the reader.

Neither input has to come from a store. Missing fields degrade to "not
recorded", and a payload with no recorded format version is compared as far as
its fields allow. An unsupported recorded format version raises
`IncompatibleSnapshotError`.

Layers
------
- [diff_snapshots][dstrack.diff.diff_snapshots] is the whole public entry
  point.
- [SnapshotDiff.to_dict][dstrack.diff.SnapshotDiff.to_dict] renders that result
  as plain JSON-serializable data, for a pipeline to consume.
- [DiffOptions][dstrack.diff.DiffOptions] carries the thresholds that decide
  unchanged from changed from significant; [preset][dstrack.diff.preset]
  returns the named sensitivities the CLI offers.

Examples:
    ```python
    >>> from dstrack.diff import diff_snapshots
    >>> old = {
    ...     "dataset_name": "customers",
    ...     "num_rows": 100,
    ...     "columns": [{"name": "age", "dtype": "int64", "nullable": True}],
    ...     "column_stats": {
    ...         "age": {"null_count": 0, "null_fraction": 0.0, "mean": 30.0, "std": 5.0}
    ...     },
    ... }
    >>> new = {
    ...     "dataset_name": "customers",
    ...     "num_rows": 150,
    ...     "columns": [{"name": "age", "dtype": "float64", "nullable": True}],
    ...     "column_stats": {
    ...         "age": {"null_count": 15, "null_fraction": 0.1, "mean": 42.0, "std": 5.0}
    ...     },
    ... }
    >>> result = diff_snapshots(old, new)
    >>> result.severity.value
    'critical'
    >>> [change.name for change in result.schema.type_changed]
    ['age']
    >>> result.summary.row_delta
    50
    >>> sorted(result.to_dict())[:3]
    ['columns', 'diff_format_version', 'identical']

    ```

    Comparing two snapshots already in a store means reading their payloads
    first, with [read_snapshot][dstrack.store.read_snapshot].
"""

from dstrack.diff._engine import diff_snapshots
from dstrack.diff._model import (
    DEFAULT_OPTIONS,
    DIFF_FORMAT_VERSION,
    PRESETS,
    ColumnChangeKind,
    ColumnKind,
    ColumnSchemaChange,
    ColumnStatDiff,
    DiffOptions,
    DiffSummary,
    DistributionShift,
    MetricDiff,
    SchemaDiff,
    SetChange,
    Severity,
    ShiftMethod,
    SnapshotDiff,
    SnapshotRef,
    preset,
    worst,
)

__all__ = [
    "DEFAULT_OPTIONS",
    "DIFF_FORMAT_VERSION",
    "PRESETS",
    "ColumnChangeKind",
    "ColumnKind",
    "ColumnSchemaChange",
    "ColumnStatDiff",
    "DiffOptions",
    "DiffSummary",
    "DistributionShift",
    "MetricDiff",
    "SchemaDiff",
    "SetChange",
    "Severity",
    "ShiftMethod",
    "SnapshotDiff",
    "SnapshotRef",
    "diff_snapshots",
    "preset",
    "worst",
]
