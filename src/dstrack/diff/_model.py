"""Result types for a snapshot comparison, and the knobs that shape it.

Pure data: nothing here computes a difference, and nothing here knows how one
is displayed. The engine fills these in, the renderers read them, and
[SnapshotDiff.to_dict][dstrack.diff._model.SnapshotDiff.to_dict] turns them
into the JSON a pipeline consumes.

Every quantity is compared through one type,
[MetricDiff][dstrack.diff._model.MetricDiff], whether it is a dataset's row
count or a single column's mean. One type means renderers and serializers have
one code path rather than one per metric family, and a metric added later needs
no new plumbing.
"""

import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

# Bumped only when a key already emitted changes meaning or disappears. Adding
# a key is not a version change: consumers are expected to ignore what they do
# not recognize.
DIFF_FORMAT_VERSION: Final = "1"
# Relative thresholds, for counts and other unbounded magnitudes.
DEFAULT_REL_TOLERANCE: Final = 1e-3
DEFAULT_ABS_TOLERANCE: Final = 1e-12
DEFAULT_WARN_PCT_CHANGE: Final = 0.10
DEFAULT_CRITICAL_PCT_CHANGE: Final = 0.50
# Absolute thresholds, for quantities that are already fractions in [0, 1].
DEFAULT_WARN_FRACTION_SHIFT: Final = 0.02
DEFAULT_CRITICAL_FRACTION_SHIFT: Final = 0.10
# The industry-standard PSI bands: below 0.1 no real shift, above 0.25 a major one.
DEFAULT_WARN_PSI: Final = 0.10
DEFAULT_CRITICAL_PSI: Final = 0.25
DEFAULT_WARN_TVD: Final = 0.10
DEFAULT_CRITICAL_TVD: Final = 0.25
DEFAULT_WARN_PERCENTILE_SHIFT: Final = 0.10
DEFAULT_CRITICAL_PERCENTILE_SHIFT: Final = 0.25


class Severity(StrEnum):
    """How much a single comparison should worry the reader."""

    UNCHANGED = "unchanged"
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# Ascending, so `worst()` can fold a group of severities into the highest one.
_SEVERITY_ORDER: Final[tuple[Severity, ...]] = (
    Severity.UNCHANGED,
    Severity.INFO,
    Severity.WARNING,
    Severity.CRITICAL,
)


def worst(severities: Iterable[Severity]) -> Severity:
    """Return the highest severity in `severities`, or ``UNCHANGED`` if empty.

    How a whole diff is classified: a group is exactly as serious as its most
    serious member, applied at every level from a column's metrics up to the
    diff itself.

    Args:
        severities: The severities to fold.

    Returns:
        The most serious of them.
    """
    return max(severities, key=_SEVERITY_ORDER.index, default=Severity.UNCHANGED)


class ColumnChangeKind(StrEnum):
    """What happened to a column between the two schemas."""

    ADDED = "added"
    REMOVED = "removed"
    TYPE_CHANGED = "type_changed"
    NULLABILITY_CHANGED = "nullability_changed"


class ColumnKind(StrEnum):
    """Which statistics variant a column's ``column_stats`` entry holds."""

    NUMERIC = "numeric"
    STRING = "string"
    DATETIME = "datetime"
    OTHER = "other"
    UNKNOWN = "unknown"


class ShiftMethod(StrEnum):
    """How a column's distribution shift was measured.

    Each measure has its own scale and its own thresholds, so a score is only
    meaningful alongside the method that produced it.
    """

    PSI = "psi"
    PERCENTILE_SHIFT = "percentile_shift"
    TOP_VALUES_TVD = "top_values_tvd"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    """Identity of one side of the comparison, as its payload recorded it.

    Every field is optional. `diff_snapshots` accepts dicts that never touched
    a store, and a payload written by another version of dstrack may omit a
    field entirely; neither is a reason to refuse to compare.

    Attributes:
        snapshot_id: Unique identifier of the snapshot.
        dataset_id: Which dataset holds the snapshot. Never present in a
            payload -- it is a store concept -- so it is supplied by the caller
            or left unset.
        parent_snapshot_id: Identifier of the snapshot this one was derived
            from, if any.
        dataset_name: Human-readable name of the dataset.
        dataset_path: Path of the tracked data, relative to the root it was
            tracked against.
        created_at: Timestamp at which the snapshot was created.
        created_by: Who or what created the snapshot.
        source_type: Kind of source the snapshot was taken from (e.g. `file`).
        source_hash: Content hash of the source data.
        schema_hash: Hash of the dataset's schema.
        format_version: Version of the snapshot payload format.
        num_rows: Number of rows in the dataset.
        num_columns: Number of columns in the dataset.
    """

    snapshot_id: str | None = None
    dataset_id: str | None = None
    parent_snapshot_id: str | None = None
    dataset_name: str | None = None
    dataset_path: str | None = None
    created_at: str | None = None
    created_by: str | None = None
    source_type: str | None = None
    source_hash: str | None = None
    schema_hash: str | None = None
    format_version: str | None = None
    num_rows: int | None = None
    num_columns: int | None = None


@dataclass(frozen=True, slots=True)
class MetricDiff:
    """One scalar quantity as it stood on each side.

    Attributes:
        key: Stable machine name, e.g. ``"num_rows"`` or ``"percentiles.p50"``.
        old: Value on the left side, or ``None`` if it was not recorded.
        new: Value on the right side, or ``None`` if it was not recorded.
        delta: ``new - old``, or ``None`` when either side is missing.
        pct_change: ``delta / abs(old)``, or ``None`` when `old` is zero or
            either side is missing. A fraction, not a percentage.
        severity: How the change classifies under the active options.
        old_display: How to show `old` when the number is not what a reader
            wants, as for a datetime bound held in epoch seconds. ``None``
            means format the number.
        new_display: The same for `new`.
    """

    key: str
    old: float | None
    new: float | None
    delta: float | None
    pct_change: float | None
    severity: Severity
    old_display: str | None = None
    new_display: str | None = None


@dataclass(frozen=True, slots=True)
class ColumnSchemaChange:
    """One column's schema entry, compared between snapshots.

    Attributes:
        name: Column name.
        kind: How the column changed (added, removed, retyped, ...).
        old_dtype: Data type on the old side, or ``None`` if absent or not
            recorded.
        new_dtype: Data type on the new side, or ``None`` if absent or not
            recorded.
        old_nullable: Nullability on the old side, or ``None`` if absent or not
            recorded.
        new_nullable: Nullability on the new side, or ``None`` if absent or not
            recorded.
        severity: How the change classifies under the active options.
    """

    name: str
    kind: ColumnChangeKind
    old_dtype: str | None
    new_dtype: str | None
    old_nullable: bool | None
    new_nullable: bool | None
    severity: Severity


@dataclass(frozen=True, slots=True)
class SchemaDiff:
    """Every difference between the two snapshots' column lists.

    Attributes:
        added: Columns present only on the new side.
        removed: Columns present only on the old side.
        type_changed: Columns on both sides whose data type differs.
        nullability_changed: Columns on both sides whose nullability differs.
        reordered: Names present on both sides at a different position.
            ``schema_hash`` is computed over *sorted* pairs, so a pure reorder
            leaves the two hashes equal; this is the only field that shows it.
        schema_hash_equal: Whether both sides recorded the same
            ``schema_hash``. Display-only: the explicit column lists are what
            the other fields are derived from.
        severity: The worst severity among the schema changes.
    """

    added: tuple[ColumnSchemaChange, ...] = ()
    removed: tuple[ColumnSchemaChange, ...] = ()
    type_changed: tuple[ColumnSchemaChange, ...] = ()
    nullability_changed: tuple[ColumnSchemaChange, ...] = ()
    reordered: tuple[str, ...] = ()
    schema_hash_equal: bool = True
    severity: Severity = Severity.UNCHANGED

    @property
    def changed(self) -> bool:
        """Whether any column was added, removed, or redefined."""
        return bool(
            self.added or self.removed or self.type_changed or self.nullability_changed
        )


@dataclass(frozen=True, slots=True)
class DistributionShift:
    """How far a column's value distribution moved.

    Attributes:
        method: Which measure `score` is on. Always read the two together.
        score: The measure's value, or ``None`` when `method` is ``NONE``.
        severity: How the shift classifies under the active options.
        added_categories: Values the new top-values list carries that the old
            one did not. User-derived: renderers must build these with
            `rich.text.Text`, and the PDF must escape them.
        removed_categories: The mirror image.
        detail: A short note on how the score was arrived at, e.g. why a
            fallback measure was used.
    """

    method: ShiftMethod
    score: float | None
    severity: Severity
    added_categories: tuple[str, ...] = ()
    removed_categories: tuple[str, ...] = ()
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ColumnStatDiff:
    """One column's statistics, compared between snapshots.

    Attributes:
        name: Column name.
        kind: The statistics variant on the new side, or on the old side when
            the column was removed.
        old_kind: The variant on the old side, when it differs from `kind`. A
            column that changed variant has no comparable statistics beyond its
            null counts, which is why such a change is always ``CRITICAL``.
        present_in_old: Whether the column has statistics on the old side.
        present_in_new: Whether the column has statistics on the new side.
        metrics: Every scalar at least one side recorded, in a stable order.
        distribution_shift: ``None`` for kinds that carry no distribution.
        severity: The worst severity among the column's metrics and shift.
    """

    name: str
    kind: ColumnKind
    old_kind: ColumnKind | None
    present_in_old: bool
    present_in_new: bool
    metrics: tuple[MetricDiff, ...]
    distribution_shift: DistributionShift | None
    severity: Severity


@dataclass(frozen=True, slots=True)
class SetChange:
    """A set-valued dataset field, e.g. ``constant_columns``.

    Attributes:
        key: Name of the field that changed.
        added: Members present only on the new side.
        removed: Members present only on the old side.
        severity: How the change classifies under the active options.
    """

    key: str
    added: tuple[str, ...]
    removed: tuple[str, ...]
    severity: Severity


@dataclass(frozen=True, slots=True)
class DiffSummary:
    """Headline counts, for a one-line render and a report's summary block.

    Attributes:
        columns_added: Number of columns present only on the new side.
        columns_removed: Number of columns present only on the old side.
        columns_type_changed: Number of columns whose data type changed.
        columns_drifted: Columns whose own severity reached ``WARNING`` or
            above, counting statistical movement rather than schema edits.
        row_delta: Change in row count (new minus old), or ``None`` when either
            side did not record one.
        severity: The overall severity of the comparison.
    """

    columns_added: int
    columns_removed: int
    columns_type_changed: int
    columns_drifted: int
    row_delta: int | None
    severity: Severity


@dataclass(frozen=True, slots=True)
class DiffOptions:
    """Thresholds deciding unchanged vs. changed vs. significant.

    Every threshold is a fraction, never a percentage.

    Relative thresholds apply to counts and unbounded magnitudes. Absolute
    *shift* thresholds apply to quantities that are already fractions in
    ``[0, 1]``, where relative change is meaningless near zero: a null fraction
    going from 0.001 to 0.002 is a 100% relative change and nothing at all.

    Attributes:
        rel_tolerance: Relative difference below which two numbers count as
            equal. Not applied to a datetime column's ``min`` and ``max``: as
            epoch seconds, it would hide shifts of several days.
        abs_tolerance: Absolute difference below which two numbers count as
            equal; covers comparisons against zero, where a relative
            tolerance is meaningless.
        warn_pct_change: Relative change in a count or magnitude at which it
            is flagged as a warning.
        critical_pct_change: Relative change in a count or magnitude at which
            it is flagged as critical.
        warn_fraction_shift: Absolute shift in a ``[0, 1]`` fraction (e.g. a
            null fraction) at which it is flagged as a warning.
        critical_fraction_shift: Absolute shift in a ``[0, 1]`` fraction at
            which it is flagged as critical.
        warn_psi: Population stability index (PSI) at which a numeric
            distribution shift is a warning.
        critical_psi: Population stability index at which a numeric
            distribution shift is critical.
        warn_tvd: Total variation distance at which a categorical
            distribution shift is a warning.
        critical_tvd: Total variation distance at which a categorical
            distribution shift is critical.
        warn_percentile_shift: Largest percentile move, as a fraction of the
            baseline's p5-p95 spread, at which a shift is a warning. Used
            when histograms are unusable for PSI.
        critical_percentile_shift: Same measure, at which a shift is
            critical.
    """

    rel_tolerance: float = DEFAULT_REL_TOLERANCE
    abs_tolerance: float = DEFAULT_ABS_TOLERANCE
    warn_pct_change: float = DEFAULT_WARN_PCT_CHANGE
    critical_pct_change: float = DEFAULT_CRITICAL_PCT_CHANGE
    warn_fraction_shift: float = DEFAULT_WARN_FRACTION_SHIFT
    critical_fraction_shift: float = DEFAULT_CRITICAL_FRACTION_SHIFT
    warn_psi: float = DEFAULT_WARN_PSI
    critical_psi: float = DEFAULT_CRITICAL_PSI
    warn_tvd: float = DEFAULT_WARN_TVD
    critical_tvd: float = DEFAULT_CRITICAL_TVD
    warn_percentile_shift: float = DEFAULT_WARN_PERCENTILE_SHIFT
    critical_percentile_shift: float = DEFAULT_CRITICAL_PERCENTILE_SHIFT


DEFAULT_OPTIONS: Final = DiffOptions()

# Named sensitivities, so the CLI can offer a choice without exposing twelve
# float flags. `low` is for datasets that move constantly and where only a
# structural break matters; `high` is for a curated table that should not move
# at all.
PRESETS: Final[dict[str, DiffOptions]] = {
    "low": DiffOptions(
        warn_pct_change=0.25,
        critical_pct_change=1.00,
        warn_fraction_shift=0.05,
        critical_fraction_shift=0.20,
        warn_psi=0.25,
        critical_psi=0.50,
        warn_tvd=0.25,
        critical_tvd=0.50,
        warn_percentile_shift=0.25,
        critical_percentile_shift=0.50,
    ),
    "normal": DEFAULT_OPTIONS,
    "high": DiffOptions(
        warn_pct_change=0.02,
        critical_pct_change=0.10,
        warn_fraction_shift=0.005,
        critical_fraction_shift=0.02,
        warn_psi=0.05,
        critical_psi=0.10,
        warn_tvd=0.05,
        critical_tvd=0.10,
        warn_percentile_shift=0.05,
        critical_percentile_shift=0.10,
    ),
}


def preset(name: str) -> DiffOptions:
    """Return a named sensitivity preset.

    Args:
        name: One of the keys of [PRESETS][dstrack.diff._model.PRESETS].

    Returns:
        The thresholds that preset stands for.

    Raises:
        KeyError: If no preset goes by that name.
    """
    try:
        return PRESETS[name]
    except KeyError:
        known = ", ".join(sorted(PRESETS))
        raise KeyError(
            f"Unknown sensitivity {name!r}. Known presets: {known}."
        ) from None


@dataclass(frozen=True, slots=True)
class SnapshotDiff:
    """Everything that changed between two snapshots.

    Attributes:
        identical: Whether every comparison came back ``UNCHANGED`` and no
            column was added, removed, or redefined. Not the same as
            `source_hash_equal`: two snapshots of one unchanged file still
            differ in ``created_at`` and ``snapshot_id``, which are identity
            rather than content and are never compared.
        same_dataset: Whether both sides belong to the same dataset. ``False``
            is legitimate -- comparing two datasets is a supported use -- and
            is surfaced rather than raised on.
        source_hash_equal: Whether both sides recorded the same non-empty
            source hash, i.e. the bytes behind them are known to be identical.
        old: The baseline snapshot.
        new: The snapshot compared against the baseline.
        schema: Column additions, removals and changes between the schemas.
        metrics: Dataset-level scalar quantities (e.g. row count), compared.
        sets: Changes to set-valued dataset fields, e.g. ``constant_columns``.
        columns: Per-column statistics, compared.
        summary: Counts of changes by kind and severity.
        severity: The highest severity across every comparison.
        options: The thresholds the diff was classified with.
    """

    old: SnapshotRef
    new: SnapshotRef
    identical: bool
    same_dataset: bool
    source_hash_equal: bool
    schema: SchemaDiff
    metrics: tuple[MetricDiff, ...]
    sets: tuple[SetChange, ...]
    columns: tuple[ColumnStatDiff, ...]
    summary: DiffSummary
    severity: Severity
    options: DiffOptions = field(default=DEFAULT_OPTIONS)

    def to_dict(self) -> dict[str, Any]:
        """Return the diff as plain JSON-serializable data.

        Enum members are flattened to their string values and tuples become
        lists, so the result survives `json.dumps` with no custom encoder and
        compares equal to a literal in a test.

        Returns:
            A dict carrying ``diff_format_version`` plus every field of this
            object. Keys are stable within a
            [DIFF_FORMAT_VERSION][dstrack.diff._model.DIFF_FORMAT_VERSION]:
            new ones may appear, existing ones are not renamed or removed.
        """
        return {
            "diff_format_version": DIFF_FORMAT_VERSION,
            **dataclasses.asdict(self, dict_factory=_plain),
        }


def _plain(items: list[tuple[str, Any]]) -> dict[str, Any]:
    """`dict_factory` that unwraps enum members to their string values.

    Args:
        items: The field name/value pairs `dataclasses.asdict` collected.

    Returns:
        The same pairs as a dict, with every `StrEnum` replaced by its value so
        the result is JSON-serializable without a custom encoder.
    """
    return {key: (v.value if isinstance(v, StrEnum) else v) for key, v in items}
