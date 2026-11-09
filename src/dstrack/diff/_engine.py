"""Comparing two snapshot payloads and classifying what changed.

Three kinds of comparison happen here, and they are kept apart on purpose.

*Structural* comparison is exact: a column is in both column lists or it is
not, and its dtype either matches or it does not. Nothing is inferred and
nothing is thresholded.

*Scalar* comparison asks how far a number moved, and needs to know what kind of
number it is. A count is judged by its relative change; a quantity already
expressed as a fraction of one is judged by its absolute shift, because
relative change is meaningless near zero -- a null fraction going from 0.001 to
0.002 has doubled and means nothing.

*Distributional* comparison asks whether a column's values still fall where
they used to, which no single scalar answers. Each column kind gets the measure
its recorded statistics can support, and every score is reported alongside the
[ShiftMethod][dstrack.diff._model.ShiftMethod] that produced it, since the
scales are not comparable to each other.
"""

import dataclasses
import math
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from dstrack.diff import _extract
from dstrack.diff._model import (
    DEFAULT_OPTIONS,
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
    worst,
)
from dstrack.errors import IncompatibleSnapshotError
from dstrack.snapshot._version import SUPPORTED_FORMAT_VERSIONS

# Metrics that are already fractions of one, and so are judged by how far they
# moved in absolute terms rather than by their relative change.
_FRACTION_METRICS: Final[frozenset[str]] = frozenset(
    {
        "null_fraction",
        "duplicate_row_fraction",
        "top_values_coverage",
        "near_duplicate_estimate",
    }
)

# Dataset-level scalars, in the order they are rendered.
_DATASET_METRICS: Final[tuple[str, ...]] = (
    "num_rows",
    "num_columns",
    "duplicate_row_fraction",
    "near_duplicate_estimate",
)

# Set-valued dataset fields. Joining one is a quality signal worth acting on;
# leaving it is good news, and is reported without raising the alarm.
_SET_FIELDS: Final[tuple[str, ...]] = ("constant_columns", "high_null_columns")

_NULL_METRICS: Final[tuple[str, ...]] = ("null_fraction", "null_count")
_PERCENTILES: Final[tuple[str, ...]] = ("p5", "p25", "p50", "p75", "p95", "p99")
_NUMERIC_METRICS: Final[tuple[str, ...]] = (
    *_NULL_METRICS,
    "num_unique",
    "mean",
    "std",
    "min",
    "max",
)
_STRING_METRICS: Final[tuple[str, ...]] = (
    *_NULL_METRICS,
    "num_unique",
    "top_values_coverage",
    "avg_char_length",
    "min_char_length",
    "max_char_length",
    "avg_token_count",
    "min_token_count",
    "max_token_count",
)
# Compared as epoch seconds, where a relative tolerance would scale with the
# distance from 1970 rather than with anything about the data.
_DATETIME_BOUNDS: Final[tuple[str, ...]] = ("min", "max")
_DATETIME_METRICS: Final[tuple[str, ...]] = (
    *_NULL_METRICS,
    *_DATETIME_BOUNDS,
    "range_days",
)

# Smoothing for PSI. A bucket empty on one side makes the log term infinite, so
# every bucket is credited half an observation before the counts are turned into
# proportions -- the Jeffreys prior. A fixed floor would do as well at keeping
# the arithmetic finite, but not at keeping the answer meaningful: it treats an
# empty bucket in a four-row snapshot as being as certainly empty as one in a
# four-million-row snapshot, and reports the first as near-total drift. Half an
# observation is instead a large share of a tiny sample and a negligible one of
# a large sample, which is exactly how confident the emptiness is.
_PSI_PRIOR: Final = 0.5

# Slack for float rounding when checking that counts fit within the row count.
_TOLERANCE: Final = 1e-6
_MIN_HISTOGRAM_EDGES: Final = 2
# Below this a measured shift is floating-point noise, not movement. Two
# identical histograms can score a few multiples of epsilon rather than a
# clean zero, and that must still read as "nothing changed".
_NEGLIGIBLE: Final = 1e-9


def diff_snapshots(
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    *,
    options: DiffOptions | None = None,
    old_dataset_id: str | None = None,
    new_dataset_id: str | None = None,
) -> SnapshotDiff:
    """Compare two snapshot payloads.

    Args:
        old: The earlier snapshot's payload, i.e. the baseline.
        new: The later snapshot's payload.
        options: Thresholds deciding what counts as a significant change.
            Defaults to [DEFAULT_OPTIONS][dstrack.diff._model.DEFAULT_OPTIONS].
        old_dataset_id: Dataset the baseline belongs to, if known. Never
            present in a payload, so it can only come from whoever read it.
        new_dataset_id: Dataset the later snapshot belongs to, if known.

    Returns:
        Everything that differs between them, each difference classified by
        severity.

    Raises:
        IncompatibleSnapshotError: If either payload records a snapshot format
            version this dstrack cannot read. Any supported version is accepted,
            including two different ones, and a payload that records none is
            compared as far as its fields allow.
    """
    active = options if options is not None else DEFAULT_OPTIONS

    old_ref = _extract.snapshot_ref(old, dataset_id=old_dataset_id)
    new_ref = _extract.snapshot_ref(new, dataset_id=new_dataset_id)
    _check_format_version(old_ref)
    _check_format_version(new_ref)

    schema = _diff_schema(old, new, old_ref=old_ref, new_ref=new_ref)
    metrics = _diff_dataset_metrics(old, new, options=active)
    sets = _diff_sets(old, new)
    columns = _diff_columns(old, new, options=active)

    severity = worst(
        [
            schema.severity,
            worst(m.severity for m in metrics),
            worst(s.severity for s in sets),
            worst(c.severity for c in columns),
        ]
    )
    return SnapshotDiff(
        old=old_ref,
        new=new_ref,
        identical=severity is Severity.UNCHANGED and not schema.changed,
        same_dataset=_same_dataset(old_ref, new_ref),
        source_hash_equal=_hashes_equal(old_ref.source_hash, new_ref.source_hash),
        schema=schema,
        metrics=metrics,
        sets=sets,
        columns=columns,
        summary=_summarize(schema, columns, metrics, severity=severity),
        severity=severity,
        options=active,
    )


def _check_format_version(ref: SnapshotRef) -> None:
    """Raise if a snapshot records a format version that cannot be compared.

    Only a version that is *recorded and unknown* is refused. Each side is
    checked against every version this dstrack reads, not against the one it
    writes, so snapshots taken before an upgrade stay comparable -- with each
    other and with snapshots taken after it. A payload recording none is a
    hand-built dict, which is a supported input; refusing it would make the
    library unusable for anything but stored snapshots.

    Args:
        ref: The identity fields read from one side.

    Raises:
        IncompatibleSnapshotError: If the recorded version is not one this
            dstrack can read, typically because a newer dstrack wrote it.
    """
    if (
        ref.format_version is not None
        and ref.format_version not in SUPPORTED_FORMAT_VERSIONS
    ):
        supported = ", ".join(repr(v) for v in sorted(SUPPORTED_FORMAT_VERSIONS))
        raise IncompatibleSnapshotError(
            f"Snapshot {ref.snapshot_id or '<unknown>'} is in format version "
            f"{ref.format_version!r}, which this dstrack cannot read "
            f"(supported: {supported}). It may have been written by a newer "
            "dstrack."
        )


def _same_dataset(old: SnapshotRef, new: SnapshotRef) -> bool:
    """Return whether both sides are known to belong to the same dataset.

    Args:
        old: Identity of the baseline.
        new: Identity of the later snapshot.

    Returns:
        ``True`` when both carry the same dataset id, or when neither carries
        one at all -- two hand-built payloads are taken at face value as two
        versions of one thing, which is what a caller comparing them means.
    """
    if old.dataset_id is None and new.dataset_id is None:
        return True
    return old.dataset_id == new.dataset_id


def _hashes_equal(old: str | None, new: str | None) -> bool:
    """Return whether two recorded source hashes prove identical bytes.

    Args:
        old: The baseline's ``source_hash``.
        new: The later snapshot's ``source_hash``.

    Returns:
        ``True`` only if both are present, non-empty, and equal. An empty hash
        is what a non-file source records, and two of them say nothing about
        whether the sources matched.
    """
    return bool(old) and bool(new) and old == new


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def _diff_schema(
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    *,
    old_ref: SnapshotRef,
    new_ref: SnapshotRef,
) -> SchemaDiff:
    """Compare the two snapshots' column lists.

    A renamed column reads as one removal and one addition. Snapshots carry no
    row-level identity, so the only way to guess otherwise is "same dtype,
    similar statistics", which cannot tell a rename from a genuine drop-and-add
    of two similar features -- and a wrong guess silently corrupts the
    per-column drift table, which is the part a reader acts on.

    Args:
        old: The baseline payload.
        new: The later payload.
        old_ref: Identity fields already read from `old`.
        new_ref: Identity fields already read from `new`.

    Returns:
        Every schema-level difference, classified.
    """
    old_columns = _extract.column_schema(old)
    new_columns = _extract.column_schema(new)

    added = tuple(
        ColumnSchemaChange(
            name=name,
            kind=ColumnChangeKind.ADDED,
            old_dtype=None,
            new_dtype=entry.dtype,
            old_nullable=None,
            new_nullable=entry.nullable,
            severity=Severity.WARNING,
        )
        for name, entry in new_columns.items()
        if name not in old_columns
    )
    removed = tuple(
        ColumnSchemaChange(
            name=name,
            kind=ColumnChangeKind.REMOVED,
            old_dtype=entry.dtype,
            new_dtype=None,
            old_nullable=entry.nullable,
            new_nullable=None,
            # Critical rather than merely notable: anything reading this
            # dataset by name breaks outright, where an addition it ignores.
            severity=Severity.CRITICAL,
        )
        for name, entry in old_columns.items()
        if name not in new_columns
    )

    type_changed: list[ColumnSchemaChange] = []
    nullability_changed: list[ColumnSchemaChange] = []
    reordered: list[str] = []
    for name, new_entry in new_columns.items():
        old_entry = old_columns.get(name)
        if old_entry is None:
            continue
        if old_entry.dtype != new_entry.dtype:
            type_changed.append(
                ColumnSchemaChange(
                    name=name,
                    kind=ColumnChangeKind.TYPE_CHANGED,
                    old_dtype=old_entry.dtype,
                    new_dtype=new_entry.dtype,
                    old_nullable=old_entry.nullable,
                    new_nullable=new_entry.nullable,
                    severity=Severity.CRITICAL,
                )
            )
        elif old_entry.nullable != new_entry.nullable:
            nullability_changed.append(
                ColumnSchemaChange(
                    name=name,
                    kind=ColumnChangeKind.NULLABILITY_CHANGED,
                    old_dtype=old_entry.dtype,
                    new_dtype=new_entry.dtype,
                    old_nullable=old_entry.nullable,
                    new_nullable=new_entry.nullable,
                    severity=Severity.INFO,
                )
            )
        if old_entry.position != new_entry.position:
            reordered.append(name)

    changes = (*added, *removed, *type_changed, *nullability_changed)
    return SchemaDiff(
        added=added,
        removed=removed,
        type_changed=tuple(type_changed),
        nullability_changed=tuple(nullability_changed),
        reordered=tuple(reordered),
        schema_hash_equal=_schema_hashes_equal(old_ref, new_ref, changes),
        severity=worst(c.severity for c in changes),
    )


def _schema_hashes_equal(
    old: SnapshotRef, new: SnapshotRef, changes: Sequence[ColumnSchemaChange]
) -> bool:
    """Return whether both sides describe the same schema.

    Args:
        old: Identity of the baseline.
        new: Identity of the later snapshot.
        changes: The column-level differences already found.

    Returns:
        The recorded hashes' equality when both sides recorded one, else
        whether any column-level difference was found. The hash is only a
        shortcut; the column lists are what the rest of the diff is built from.
    """
    if old.schema_hash and new.schema_hash:
        return old.schema_hash == new.schema_hash
    return not changes


# ---------------------------------------------------------------------------
# Scalars
# ---------------------------------------------------------------------------


def _diff_dataset_metrics(
    old: Mapping[str, Any], new: Mapping[str, Any], *, options: DiffOptions
) -> tuple[MetricDiff, ...]:
    """Compare the dataset-level scalars both payloads carry.

    Args:
        old: The baseline payload.
        new: The later payload.
        options: The active thresholds.

    Returns:
        One entry per metric at least one side recorded, in render order. A
        metric neither side recorded is omitted entirely, which is how fields
        the current builders never produce -- ``near_duplicate_estimate`` and
        the row sketches -- are tolerated without being special-cased.
    """
    return tuple(
        diff
        for key in _DATASET_METRICS
        if (
            diff := _compare(
                key,
                _extract.number(old.get(key)),
                _extract.number(new.get(key)),
                options=options,
            )
        )
        is not None
    )


def _compare(
    key: str,
    old: float | None,
    new: float | None,
    *,
    options: DiffOptions,
    datetime_bound: bool = False,
) -> MetricDiff | None:
    """Compare one scalar and classify how far it moved.

    Args:
        key: The metric's stable name, which decides how it is judged.
        old: The baseline value, or ``None`` if not recorded.
        new: The later value, or ``None`` if not recorded.
        options: The active thresholds.
        datetime_bound: Whether the values are a datetime column's bound in
            epoch seconds, which get no relative tolerance. The key alone
            cannot say: numeric columns have a ``min`` and ``max`` too.

    Returns:
        The comparison, or ``None`` if neither side recorded the metric and
        there is therefore nothing to say about it.
    """
    if old is None and new is None:
        return None
    if old is None or new is None:
        # A metric appearing or disappearing is worth showing, but it is a
        # change in what was recorded, not a change in the data.
        return MetricDiff(
            key=key,
            old=old,
            new=new,
            delta=None,
            pct_change=None,
            severity=Severity.INFO,
        )

    delta = new - old
    pct_change = delta / abs(old) if old != 0 else None

    rel_tol = 0.0 if datetime_bound else options.rel_tolerance
    if math.isclose(old, new, rel_tol=rel_tol, abs_tol=options.abs_tolerance):
        return MetricDiff(
            key=key,
            old=old,
            new=new,
            delta=delta,
            pct_change=pct_change,
            severity=Severity.UNCHANGED,
        )

    severity = _classify(key, delta=delta, pct_change=pct_change, options=options)
    if datetime_bound:
        # Relative change of an epoch value is tiny whatever moved -- a day is
        # ~5e-5 of a present-day timestamp, a second ~6e-10 -- so it can fall
        # under the _NEGLIGIBLE floor in `_band`. The bound has already been
        # judged different by `abs_tolerance`; that verdict must stand.
        severity = worst([severity, Severity.INFO])
    return MetricDiff(
        key=key,
        old=old,
        new=new,
        delta=delta,
        pct_change=pct_change,
        severity=severity,
    )


def _classify(
    key: str, *, delta: float, pct_change: float | None, options: DiffOptions
) -> Severity:
    """Grade a change that is already known to be real.

    Args:
        key: The metric's stable name.
        delta: How far the value moved.
        pct_change: The move as a fraction of the baseline, or ``None`` when
            the baseline was zero.
        options: The active thresholds.

    Returns:
        The severity to report.
    """
    if key in _FRACTION_METRICS:
        return _band(
            abs(delta), options.warn_fraction_shift, options.critical_fraction_shift
        )
    if pct_change is None:
        # Zero to something. Structurally notable -- no rows to some rows, no
        # nulls to some nulls -- and on no relative scale that could grade it.
        return Severity.WARNING
    return _band(abs(pct_change), options.warn_pct_change, options.critical_pct_change)


def _band(magnitude: float, warn: float, critical: float) -> Severity:
    """Place a magnitude in a severity band.

    Args:
        magnitude: The absolute size of the change.
        warn: Where ``WARNING`` starts.
        critical: Where ``CRITICAL`` starts.

    Returns:
        The band it falls in. Anything real but below `warn` is ``INFO``: it
        happened, and the reader decides whether it matters. A magnitude at
        the floor is ``UNCHANGED``, which is what keeps a snapshot compared
        against itself reporting as identical -- the distribution measures are
        graded here directly, and for them zero means zero rather than "too
        small to have been filtered out already".
    """
    if magnitude <= _NEGLIGIBLE:
        return Severity.UNCHANGED
    if magnitude >= critical:
        return Severity.CRITICAL
    if magnitude >= warn:
        return Severity.WARNING
    return Severity.INFO


def _diff_sets(old: Mapping[str, Any], new: Mapping[str, Any]) -> tuple[SetChange, ...]:
    """Compare the set-valued dataset fields.

    Args:
        old: The baseline payload.
        new: The later payload.

    Returns:
        One entry per field whose membership changed. A column *joining*
        ``constant_columns`` or ``high_null_columns`` is a warning -- a feature
        just went dead or mostly-null -- while one leaving is reported as
        information, since a column that started varying again is good news.
    """
    changes: list[SetChange] = []
    for key in _SET_FIELDS:
        before = set(_extract.string_tuple(old.get(key)))
        after = set(_extract.string_tuple(new.get(key)))
        entered = tuple(sorted(after - before))
        left = tuple(sorted(before - after))
        if not entered and not left:
            continue
        changes.append(
            SetChange(
                key=key,
                added=entered,
                removed=left,
                severity=Severity.WARNING if entered else Severity.INFO,
            )
        )
    return tuple(changes)


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------


def _diff_columns(
    old: Mapping[str, Any], new: Mapping[str, Any], *, options: DiffOptions
) -> tuple[ColumnStatDiff, ...]:
    """Compare every column's statistics.

    Args:
        old: The baseline payload.
        new: The later payload.
        options: The active thresholds.

    Returns:
        One entry per column either side recorded statistics for, ordered by
        the new schema and then by name, so the output reads in the order the
        dataset's columns actually appear.
    """
    old_stats = _extract.column_stats(old)
    new_stats = _extract.column_stats(new)
    old_schema = _extract.column_schema(old)
    new_schema = _extract.column_schema(new)

    ordered = list(new_schema)
    ordered += sorted(set(new_stats) - set(ordered))
    ordered += sorted((set(old_stats) | set(old_schema)) - set(ordered))

    return tuple(
        _diff_column(
            name,
            old_stats.get(name),
            new_stats.get(name),
            old_dtype=old_schema[name].dtype if name in old_schema else None,
            new_dtype=new_schema[name].dtype if name in new_schema else None,
            old_rows=_extract.integer(old.get("num_rows")),
            new_rows=_extract.integer(new.get("num_rows")),
            options=options,
        )
        for name in ordered
        if name in old_stats or name in new_stats
    )


def _diff_column(
    name: str,
    old: Mapping[str, Any] | None,
    new: Mapping[str, Any] | None,
    *,
    old_dtype: str | None,
    new_dtype: str | None,
    old_rows: int | None,
    new_rows: int | None,
    options: DiffOptions,
) -> ColumnStatDiff:
    """Compare one column's statistics.

    Args:
        name: The column's name.
        old: Its baseline statistics, or ``None`` if the column is new.
        new: Its later statistics, or ``None`` if the column was dropped.
        old_dtype: Its baseline dtype, used only as a hint about which
            statistics variant to expect.
        new_dtype: Its later dtype.
        old_rows: The baseline's row count, for normalizing category counts.
        new_rows: The later row count.
        options: The active thresholds.

    Returns:
        The column's comparison. When the two sides hold different statistics
        variants, only the null counts are comparable, and the change is
        reported as critical: the column now holds a different kind of value.
    """
    old_kind = _extract.column_kind(old, old_dtype) if old is not None else None
    new_kind = _extract.column_kind(new, new_dtype) if new is not None else None
    kind = new_kind if new_kind is not None else (old_kind or ColumnKind.UNKNOWN)

    kind_changed = (
        old_kind is not None and new_kind is not None and old_kind != new_kind
    )
    keys = _NULL_METRICS if kind_changed else _metric_keys(kind)

    metrics = tuple(
        diff
        for key in keys
        if (diff := _compare_stat(key, old, new, kind=kind, options=options))
        is not None
    )
    shift = (
        None
        if kind_changed or old is None or new is None
        else _distribution_shift(
            kind,
            old,
            new,
            old_rows=old_rows,
            new_rows=new_rows,
            options=options,
        )
    )

    severities = [m.severity for m in metrics]
    if shift is not None:
        severities.append(shift.severity)
    if kind_changed:
        severities.append(Severity.CRITICAL)
    elif old is None or new is None:
        # The schema diff already reports the add or drop with its own
        # severity; repeating it here would double-count it in the roll-up.
        severities.append(Severity.UNCHANGED)

    return ColumnStatDiff(
        name=name,
        kind=kind,
        old_kind=old_kind if kind_changed else None,
        present_in_old=old is not None,
        present_in_new=new is not None,
        metrics=metrics,
        distribution_shift=shift,
        severity=worst(severities),
    )


def _compare_stat(
    key: str,
    old: Mapping[str, Any] | None,
    new: Mapping[str, Any] | None,
    *,
    kind: ColumnKind,
    options: DiffOptions,
) -> MetricDiff | None:
    """Compare one statistic of a column.

    Args:
        key: The metric's stable name.
        old: The column's baseline statistics, or ``None``.
        new: The column's later statistics, or ``None``.
        kind: The statistics variant being compared.
        options: The active thresholds.

    Returns:
        The comparison, or ``None`` if neither side recorded the metric. A
        datetime bound also carries each side's readable form, taken from the
        recorded text: rebuilding it from epoch seconds would show a
        timezone-less value in whichever zone the machine happens to be in.
    """
    datetime_bound = kind is ColumnKind.DATETIME and key in _DATETIME_BOUNDS
    diff = _compare(
        key,
        _stat(old, key),
        _stat(new, key),
        options=options,
        datetime_bound=datetime_bound,
    )
    if diff is None or not datetime_bound:
        return diff
    return dataclasses.replace(
        diff,
        old_display=_timestamp_display(old, key),
        new_display=_timestamp_display(new, key),
    )


def _metric_keys(kind: ColumnKind) -> tuple[str, ...]:
    """Return the scalars worth comparing for a statistics variant.

    Args:
        kind: The variant the column's entry holds.

    Returns:
        The metric keys, in render order.
    """
    if kind is ColumnKind.NUMERIC:
        return (*_NUMERIC_METRICS, *(f"percentiles.{p}" for p in _PERCENTILES))
    if kind is ColumnKind.STRING:
        return _STRING_METRICS
    if kind is ColumnKind.DATETIME:
        return _DATETIME_METRICS
    return _NULL_METRICS


def _stat(stats: Mapping[str, Any] | None, key: str) -> float | None:
    """Read one statistic out of a column's entry.

    Args:
        stats: The column's statistics, or ``None``.
        key: The metric's name, dotted for a nested field such as
            ``"percentiles.p50"``.

    Returns:
        The value as a float, or ``None`` if it is absent or unusable.
        Datetime bounds are ISO strings rather than numbers, and are converted
        to epoch seconds so they compare like any other scalar.
    """
    if stats is None:
        return None

    head, _, tail = key.partition(".")
    if tail:
        nested = stats.get(head)
        return (
            _extract.number(nested.get(tail)) if isinstance(nested, Mapping) else None
        )

    value = stats.get(key)
    if key in ("min", "max") and isinstance(value, str):
        return _epoch_seconds(value)
    return _extract.number(value)


def _epoch_seconds(value: str) -> float | None:
    """Parse an ISO-8601 timestamp into epoch seconds.

    Args:
        value: The recorded timestamp, as a datetime column's bound records it.

    Returns:
        The moment as epoch seconds, or ``None`` if it cannot be parsed. A
        trailing ``Z`` is translated first, matching how the stats computer
        reads these values back. A timestamp without an offset is read as UTC,
        as recorded timestamps are elsewhere; ``datetime.timestamp`` would
        otherwise apply the local zone and daylight-saving rules of whichever
        machine runs the comparison.
    """
    parsed = _parse_timestamp(value)
    if parsed is None:
        return None
    aware = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    return aware.timestamp()


def _parse_timestamp(value: str) -> datetime | None:
    """Parse a recorded ISO-8601 timestamp.

    Args:
        value: The recorded text.

    Returns:
        The moment, or ``None`` if the text cannot be parsed.
    """
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _timestamp_display(stats: Mapping[str, Any] | None, key: str) -> str | None:
    """Return a datetime bound as a reader should see it.

    Args:
        stats: The column's statistics, or ``None``.
        key: Which bound to read.

    Returns:
        The moment to the precision recorded, keeping its UTC offset and
        writing UTC as ``Z``. ``None`` if the bound is absent or invalid,
        where there is no moment to show.
    """
    value = stats.get(key) if stats is not None else None
    parsed = _parse_timestamp(value) if isinstance(value, str) else None
    if parsed is None:
        return None
    text = parsed.isoformat(sep=" ")
    return f"{text.removesuffix('+00:00')}Z" if text.endswith("+00:00") else text


# ---------------------------------------------------------------------------
# Distributions
# ---------------------------------------------------------------------------


def _distribution_shift(
    kind: ColumnKind,
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    *,
    old_rows: int | None,
    new_rows: int | None,
    options: DiffOptions,
) -> DistributionShift | None:
    """Measure how far a column's value distribution moved.

    Args:
        kind: The statistics variant, which decides the measure.
        old: The column's baseline statistics.
        new: The column's later statistics.
        old_rows: The baseline's row count.
        new_rows: The later row count.
        options: The active thresholds.

    Returns:
        The shift, or ``None`` for a variant that records no distribution at
        all -- a datetime or unrecognized column has bounds, not a shape.
    """
    if kind is ColumnKind.NUMERIC:
        return _numeric_shift(old, new, options=options)
    if kind is ColumnKind.STRING:
        return _string_shift(
            old, new, old_rows=old_rows, new_rows=new_rows, options=options
        )
    return None


def _numeric_shift(
    old: Mapping[str, Any], new: Mapping[str, Any], *, options: DiffOptions
) -> DistributionShift:
    """Measure a numeric column's shift, preferring PSI over its histogram.

    Args:
        old: The column's baseline statistics.
        new: The column's later statistics.
        options: The active thresholds.

    Returns:
        A PSI score when both histograms are usable, else a percentile-based
        fallback, else a ``NONE`` shift saying nothing could be measured.
    """
    psi = _population_stability_index(old.get("histogram"), new.get("histogram"))
    if psi is not None:
        return DistributionShift(
            method=ShiftMethod.PSI,
            score=psi,
            severity=_band(psi, options.warn_psi, options.critical_psi),
        )

    fallback = _percentile_shift(old.get("percentiles"), new.get("percentiles"))
    if fallback is not None:
        return DistributionShift(
            method=ShiftMethod.PERCENTILE_SHIFT,
            score=fallback,
            severity=_band(
                fallback,
                options.warn_percentile_shift,
                options.critical_percentile_shift,
            ),
            detail="histogram unusable; compared on percentiles",
        )

    return DistributionShift(
        method=ShiftMethod.NONE,
        score=None,
        severity=Severity.UNCHANGED,
        detail="no comparable distribution recorded",
    )


def _population_stability_index(old: Any, new: Any) -> float | None:
    """Return the PSI between two histograms, or ``None`` if it cannot be computed.

    The two histograms rarely share bin edges, since each snapshot's edges are
    derived from its own minimum and maximum. Counts cannot be re-binned
    exactly, so each source bin's count is spread across the bins of a shared
    grid in proportion to how much of it they overlap -- the piecewise-uniform
    density assumption, which is the standard choice and needs no dependency.

    Args:
        old: The baseline ``histogram`` mapping.
        new: The later ``histogram`` mapping.

    Returns:
        The PSI, or ``None`` when either histogram is unusable: too few edges,
        a count list that does not match its edges, no observations at all, or
        the single degenerate bin a constant column produces, none of which
        describe a shape that can be compared.
    """
    old_grid = _histogram(old)
    new_grid = _histogram(new)
    if old_grid is None or new_grid is None:
        return None

    old_edges, old_counts = old_grid
    new_edges, new_counts = new_grid

    lower = min(old_edges[0], new_edges[0])
    upper = max(old_edges[-1], new_edges[-1])
    union = sorted({e for e in (*old_edges, *new_edges) if lower <= e <= upper})
    union = [lower, *[e for e in union if lower < e < upper], upper]
    if len(union) < _MIN_HISTOGRAM_EDGES:
        return None

    old_p = _proportions(_redistribute(old_edges, old_counts, union))
    new_p = _proportions(_redistribute(new_edges, new_counts, union))

    return sum(
        (after - before) * math.log(after / before)
        for before, after in zip(old_p, new_p, strict=True)
    )


def _proportions(counts: Sequence[float]) -> list[float]:
    """Turn bin counts into smoothed proportions summing to one.

    Args:
        counts: One count per bin of the shared grid.

    Returns:
        The proportions, every one of them strictly positive so the PSI's
        logarithms are defined. See
        [_PSI_PRIOR][dstrack.diff._engine._PSI_PRIOR] for why the smoothing is
        a share of an observation rather than a fixed floor.
    """
    total = sum(counts) + _PSI_PRIOR * len(counts)
    return [(count + _PSI_PRIOR) / total for count in counts]


def _histogram(raw: Any) -> tuple[list[float], list[float]] | None:
    """Read a histogram, rejecting one that cannot describe a distribution.

    Args:
        raw: The recorded ``histogram`` mapping.

    Returns:
        Its edges and counts, or ``None`` if it is unusable. A single bin is
        rejected along with the malformed cases: `StatsComputer` emits one for
        a constant column, and it carries no shape to compare.
    """
    if not isinstance(raw, Mapping):
        return None
    edges = _extract.float_list(raw.get("bin_edges"))
    counts = _extract.float_list(raw.get("counts"))
    if edges is None or counts is None:
        return None
    if len(edges) < _MIN_HISTOGRAM_EDGES or len(counts) != len(edges) - 1:
        return None
    if len(counts) < _MIN_HISTOGRAM_EDGES or sum(counts) <= 0:
        return None
    if edges[-1] <= edges[0]:
        return None
    return edges, counts


def _redistribute(
    edges: Sequence[float], counts: Sequence[float], union: Sequence[float]
) -> list[float]:
    """Spread a histogram's counts onto a shared grid.

    Args:
        edges: The histogram's own bin edges, ascending.
        counts: Its per-bin counts.
        union: The shared grid's edges, ascending and spanning both histograms.

    Returns:
        One count per bin of `union`, together holding the same total as
        `counts` (up to floating-point error).
    """
    spread = [0.0] * (len(union) - 1)

    for index, count in enumerate(counts):
        low, high = edges[index], edges[index + 1]
        span = high - low
        if span <= 0:
            # A zero-width source bin cannot be subdivided, and it overlaps
            # nothing by width, so the overlap rule below would drop its count.
            # Its whole count belongs to the target bin containing its edge:
            # lower-inclusive, except that the grid's final edge belongs to the
            # last bin.
            target = min(bisect_right(union, low) - 1, len(spread) - 1)
            spread[max(target, 0)] += count
            continue
        for target in range(len(spread)):
            overlap = min(high, union[target + 1]) - max(low, union[target])
            if overlap > 0:
                spread[target] += count * (overlap / span)

    return spread


def _percentile_shift(old: Any, new: Any) -> float | None:
    """Return the largest percentile move, relative to the baseline's spread.

    Args:
        old: The baseline ``percentiles`` mapping.
        new: The later ``percentiles`` mapping.

    Returns:
        The move as a fraction of the baseline's p5-p95 spread, or ``None`` if
        the two share no comparable percentile. Falling back to ``|p50|`` and
        then to 1.0 keeps the measure defined for a column whose middle 90% is
        a single value.
    """
    if not isinstance(old, Mapping) or not isinstance(new, Mapping):
        return None

    moves = [
        abs(after - before)
        for key in _PERCENTILES
        if (before := _extract.number(old.get(key))) is not None
        and (after := _extract.number(new.get(key))) is not None
    ]
    if not moves:
        return None

    p5 = _extract.number(old.get("p5"))
    p95 = _extract.number(old.get("p95"))
    p50 = _extract.number(old.get("p50"))
    if p5 is not None and p95 is not None and p95 < p5:
        return None
    spread = (p95 - p5) if p5 is not None and p95 is not None else 0.0
    scale = spread or abs(p50 or 0.0) or 1.0
    return max(moves) / scale


def _string_shift(
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    *,
    old_rows: int | None,
    new_rows: int | None,
    options: DiffOptions,
) -> DistributionShift:
    """Measure a string column's shift as total variation distance.

    Only the top values are recorded, so the tail they do not cover is carried
    as one synthetic bucket. Without it, a column whose top values held steady
    while everything else churned would score zero.

    Args:
        old: The column's baseline statistics.
        new: The column's later statistics.
        old_rows: The baseline's row count, for turning counts into shares.
        new_rows: The later row count.
        options: The active thresholds.

    Returns:
        The distance, in ``[0, 1]``, with the categories that came and went.
    """
    old_values = _extract.counts(old.get("top_values"))
    new_values = _extract.counts(new.get("top_values"))
    if not old_values and not new_values:
        return DistributionShift(
            method=ShiftMethod.NONE,
            score=None,
            severity=Severity.UNCHANGED,
            detail="no top values recorded",
        )

    problem = _count_problem(old, old_values, rows=old_rows, side="baseline")
    problem = problem or _count_problem(new, new_values, rows=new_rows, side="later")
    if problem:
        return DistributionShift(
            method=ShiftMethod.NONE,
            score=None,
            severity=Severity.UNCHANGED,
            detail=f"top values unusable: {problem}",
        )

    old_share, old_tail, old_note = _category_shares(old, old_values, rows=old_rows)
    new_share, new_tail, new_note = _category_shares(new, new_values, rows=new_rows)

    keys = set(old_share) | set(new_share)
    # Inputs that pass validation can still total 1 + tolerance from rounding.
    distance = min(
        1.0,
        0.5
        * (
            sum(abs(new_share.get(key, 0.0) - old_share.get(key, 0.0)) for key in keys)
            + abs(new_tail - old_tail)
        ),
    )
    return DistributionShift(
        method=ShiftMethod.TOP_VALUES_TVD,
        score=distance,
        severity=_band(distance, options.warn_tvd, options.critical_tvd),
        added_categories=tuple(sorted(set(new_values) - set(old_values))),
        removed_categories=tuple(sorted(set(old_values) - set(new_values))),
        detail=old_note or new_note,
    )


def _count_problem(
    stats: Mapping[str, Any],
    values: Mapping[str, float],
    *,
    rows: int | None,
    side: str,
) -> str | None:
    """Say why recorded top values cannot form a distribution, if they cannot.

    Total variation distance is only bounded by 1 for genuine distributions.
    Scoring impossible counts would report a confident, meaningless drift.

    Args:
        stats: The column's statistics, for its null fraction and coverage.
        values: Its ``top_values`` counts.
        rows: The snapshot's row count, or ``None`` if not recorded.
        side: Which snapshot this is, for the message.

    Returns:
        A short reason, or ``None`` when the counts are usable.
    """
    if any(count < 0 for count in values.values()):
        return f"{side} snapshot has a negative count"

    coverage = _extract.number(stats.get("top_values_coverage"))
    if coverage is not None and not 0.0 <= coverage <= 1.0:
        return f"{side} snapshot has coverage outside [0, 1]"

    counted = sum(values.values())
    disagreement = f"{side} snapshot coverage disagrees with its counts"
    if coverage is not None and (counted > 0) != (coverage > 0):
        return disagreement

    if rows is not None:
        null_fraction = _extract.number(stats.get("null_fraction")) or 0.0
        non_null = float(rows) * (1.0 - null_fraction)
        if non_null > 0:
            if counted > non_null * (1 + _TOLERANCE):
                return f"{side} snapshot counts exceed its non-null rows"
            if coverage is not None and abs(coverage - counted / non_null) > _TOLERANCE:
                return disagreement
    return None


def _category_shares(
    stats: Mapping[str, Any], values: Mapping[str, float], *, rows: int | None
) -> tuple[dict[str, float], float, str | None]:
    """Turn recorded category counts into shares of the column's values.

    Args:
        stats: The column's statistics, for its null fraction and coverage.
        values: Its ``top_values`` counts.
        rows: The snapshot's row count, or ``None`` if not recorded.

    Returns:
        The shares of the top values, the share of everything outside them (kept
        apart so no category name can collide with it, and so a shift confined
        to the untracked tail stays visible), and a note when the normalization
        had to be weakened because the row count was missing.
    """
    counted = sum(values.values())
    null_fraction = _extract.number(stats.get("null_fraction")) or 0.0
    coverage = _extract.number(stats.get("top_values_coverage"))

    note: str | None = None
    non_null = float(rows) * (1.0 - null_fraction) if rows is not None else 0.0
    if non_null <= 0:
        # Top values hold only ``coverage`` of the mass, so the total stays at one.
        non_null = counted / coverage if coverage and coverage > 0 else counted
        note = "row count unavailable; shares normalized over the top values only"

    shares = (
        {key: count / non_null for key, count in values.items()} if non_null else {}
    )
    covered = (
        coverage if coverage is not None else (counted / non_null if non_null else 1.0)
    )
    return shares, max(0.0, 1.0 - covered), note


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _summarize(
    schema: SchemaDiff,
    columns: Sequence[ColumnStatDiff],
    metrics: Sequence[MetricDiff],
    *,
    severity: Severity,
) -> DiffSummary:
    """Reduce the whole comparison to the handful of numbers a reader scans first.

    Args:
        schema: The schema-level differences.
        columns: The per-column comparisons.
        metrics: The dataset-level comparisons.
        severity: The diff's overall severity.

    Returns:
        The headline counts.
    """
    rows = next((m for m in metrics if m.key == "num_rows"), None)
    drifted = sum(
        1
        for column in columns
        if column.severity in (Severity.WARNING, Severity.CRITICAL)
    )
    return DiffSummary(
        columns_added=len(schema.added),
        columns_removed=len(schema.removed),
        columns_type_changed=len(schema.type_changed),
        columns_drifted=drifted,
        row_delta=int(rows.delta)
        if rows is not None and rows.delta is not None
        else None,
        severity=severity,
    )
