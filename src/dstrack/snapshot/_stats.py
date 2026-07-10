import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from dstrack.readers import Cell, TabularReader

_NUMERIC_DTYPES: frozenset[str] = frozenset(
    {"int8", "int16", "int32", "int64", "float16", "float32", "float64", "bool"}
)
_NUM_HISTOGRAM_BINS: int = 20
_TOP_VALUES_K: int = 50


@dataclass(frozen=True, slots=True)
class PercentileStats:
    """p5-p99 percentiles of a numeric column, linearly interpolated."""

    p5: float = 0.0
    p25: float = 0.0
    p50: float = 0.0
    p75: float = 0.0
    p95: float = 0.0
    p99: float = 0.0


@dataclass(frozen=True, slots=True)
class HistogramStats:
    """Equal-width histogram of a numeric column's values."""

    bin_edges: list[float] = field(default_factory=list)
    counts: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class NumericColumnStats:
    """Statistics for an ``int*``/``float*``/``bool`` column."""

    null_count: int = 0
    null_fraction: float = 0.0
    mean: float = 0.0
    std: float = 0.0
    min: float = 0.0
    max: float = 0.0
    percentiles: PercentileStats = field(default_factory=PercentileStats)
    histogram: HistogramStats = field(default_factory=HistogramStats)
    num_unique: int = 0


@dataclass(frozen=True, slots=True)
class StringColumnStats:
    """Statistics for a ``string`` column."""

    null_count: int = 0
    null_fraction: float = 0.0
    num_unique: int = 0
    top_values: dict[str, int] = field(default_factory=dict)
    top_values_coverage: float = 0.0
    avg_char_length: float = 0.0
    min_char_length: float = 0.0
    max_char_length: float = 0.0
    avg_token_count: float = 0.0
    min_token_count: float = 0.0
    max_token_count: float = 0.0


@dataclass(frozen=True, slots=True)
class DatetimeColumnStats:
    """Statistics for a ``datetime64`` column; min/max are ISO strings."""

    null_count: int = 0
    null_fraction: float = 0.0
    min: str = ""
    max: str = ""
    range_days: float = 0.0


@dataclass(frozen=True, slots=True)
class OtherColumnStats:
    """Null-only statistics for a column whose dtype is unrecognized."""

    null_count: int = 0
    null_fraction: float = 0.0


# The per-column entry type stored in DatasetStats.column_stats, keyed by dtype category.
ColumnStats = (
    NumericColumnStats | StringColumnStats | DatetimeColumnStats | OtherColumnStats
)


@dataclass
class DatasetStats:
    """Per-column and dataset-level statistics produced by a data pass.

    Covers num_rows, column_stats, duplicate_row_fraction, constant_columns,
    and high_null_columns. Sketch-based fields (near_duplicate_estimate,
    row_minhash, row_hyperloglog) are handled by a separate builder.
    """

    num_rows: int
    column_stats: dict[str, ColumnStats]
    duplicate_row_fraction: float
    constant_columns: list[str]
    high_null_columns: list[str]


class _ColumnAcc(Protocol):
    """Common interface every per-column accumulator implements.

    ``compute`` drives all accumulators through this interface so it does not
    need to branch on column type while scanning rows or building stats.
    """

    null_count: int

    def update(self, val: Cell) -> None: ...

    def is_constant(self) -> bool: ...

    def stats(self, null_fraction: float) -> ColumnStats: ...


@dataclass
class _NumericAcc:
    """Running accumulator for an ``int*``/``float*``/``bool`` column."""

    vals: list[float] = field(default_factory=list)
    null_count: int = 0

    def update(self, val: Cell) -> None:
        self.vals.append(float(val))  # type: ignore[arg-type]

    def is_constant(self) -> bool:
        return not self.vals or min(self.vals) == max(self.vals)

    def stats(self, null_fraction: float) -> NumericColumnStats:
        return _numeric_stats(self.vals, self.null_count, null_fraction)


@dataclass
class _StringAcc:
    """Running accumulator for a ``string`` column."""

    counter: Counter[str] = field(default_factory=Counter)
    char_len_sum: int = 0
    char_len_min: float = math.inf
    char_len_max: float = -math.inf
    token_count_sum: int = 0
    token_count_min: float = math.inf
    token_count_max: float = -math.inf
    non_null_count: int = 0
    null_count: int = 0

    def update(self, val: Cell) -> None:
        s = str(val)
        self.non_null_count += 1
        self.counter[s] += 1
        cl = len(s)
        tc = len(s.split())
        self.char_len_sum += cl
        self.token_count_sum += tc
        self.char_len_min = min(self.char_len_min, cl)
        self.char_len_max = max(self.char_len_max, cl)
        self.token_count_min = min(self.token_count_min, tc)
        self.token_count_max = max(self.token_count_max, tc)

    def is_constant(self) -> bool:
        return len(self.counter) <= 1

    def stats(self, null_fraction: float) -> StringColumnStats:
        return _string_stats(self, null_fraction)


@dataclass
class _DatetimeAcc:
    """Running accumulator for a ``datetime64`` column.

    Values are compared and stored as ISO strings rather than parsed
    ``datetime`` objects, since lexicographic order matches chronological
    order for ISO 8601 timestamps.
    """

    min_val: str = ""
    max_val: str = ""
    non_null_count: int = 0
    null_count: int = 0

    def update(self, val: Cell) -> None:
        s = str(val)
        if self.non_null_count == 0 or s < self.min_val:
            self.min_val = s
        if self.non_null_count == 0 or s > self.max_val:
            self.max_val = s
        self.non_null_count += 1

    def is_constant(self) -> bool:
        return self.non_null_count == 0 or self.min_val == self.max_val

    def stats(self, null_fraction: float) -> DatetimeColumnStats:
        return _datetime_stats(self, null_fraction)


@dataclass
class _OtherAcc:
    """Accumulator for columns whose dtype is unrecognized.

    Only null counts are tracked; the value itself is never inspected.
    """

    null_count: int = 0

    def update(self, val: Cell) -> None:
        pass

    def is_constant(self) -> bool:
        return False

    def stats(self, null_fraction: float) -> OtherColumnStats:
        return OtherColumnStats(null_count=self.null_count, null_fraction=null_fraction)


class StatsComputer:
    """Computes per-column and dataset-level statistics in a single data pass.

    Handles ``int*``, ``float*``, and ``bool`` dtypes as numeric,
    ``string`` columns, and ``datetime64`` columns.  Unknown dtypes produce
    only null counts.
    """

    def compute(self, reader: TabularReader) -> DatasetStats:
        """Run a full data pass and return aggregated statistics.

        Args:
            reader: Any TabularReader whose batches will be consumed once.

        Returns:
            A populated :class:`DatasetStats` instance.
        """
        cols = reader.columns()
        col_names = [c.name for c in cols]
        col_dtypes = {c.name: c.dtype for c in cols}

        accs = self._init_accumulators(col_dtypes)

        num_rows = 0
        seen_rows: set[tuple[Cell, ...]] = set()
        duplicate_count = 0

        for batch in reader.iter_batches():
            batch_rows, batch_duplicates = self._process_batch(
                batch, col_names, accs, seen_rows
            )
            num_rows += batch_rows
            duplicate_count += batch_duplicates

        return self._build_dataset_stats(col_names, accs, num_rows, duplicate_count)

    def _init_accumulators(self, col_dtypes: dict[str, str]) -> dict[str, _ColumnAcc]:
        """Create one accumulator per column, chosen by dtype."""
        accs: dict[str, _ColumnAcc] = {}
        for name, dtype in col_dtypes.items():
            if dtype in _NUMERIC_DTYPES:
                accs[name] = _NumericAcc()
            elif dtype == "string":
                accs[name] = _StringAcc()
            elif dtype == "datetime64":
                accs[name] = _DatetimeAcc()
            else:
                accs[name] = _OtherAcc()
        return accs

    def _process_batch(
        self,
        batch: list[list[Cell]],
        col_names: list[str],
        accs: dict[str, _ColumnAcc],
        seen_rows: set[tuple[Cell, ...]],
    ) -> tuple[int, int]:
        """Feed one batch of rows into the accumulators.

        Returns the (row_count, duplicate_count) contributed by this batch;
        ``seen_rows`` is shared and updated across batches so duplicates are
        detected dataset-wide, not just within a single batch.
        """
        num_rows = 0
        duplicate_count = 0
        for row in batch:
            num_rows += 1
            row_key = tuple(row)
            if row_key in seen_rows:
                duplicate_count += 1
            else:
                seen_rows.add(row_key)

            for name, val in zip(col_names, row, strict=True):
                acc = accs[name]
                if val is None:
                    acc.null_count += 1
                else:
                    acc.update(val)
        return num_rows, duplicate_count

    def _build_dataset_stats(
        self,
        col_names: list[str],
        accs: dict[str, _ColumnAcc],
        num_rows: int,
        duplicate_count: int,
    ) -> DatasetStats:
        """Turn the fully-populated accumulators into a DatasetStats."""
        column_stats: dict[str, ColumnStats] = {}
        constant_columns: list[str] = []
        high_null_columns: list[str] = []

        for name in col_names:
            acc = accs[name]
            null_fraction = acc.null_count / num_rows if num_rows > 0 else 0.0
            if null_fraction > 0.5:
                high_null_columns.append(name)
            column_stats[name] = acc.stats(null_fraction)
            if acc.is_constant():
                constant_columns.append(name)

        return DatasetStats(
            num_rows=num_rows,
            column_stats=column_stats,
            duplicate_row_fraction=duplicate_count / num_rows if num_rows > 0 else 0.0,
            constant_columns=constant_columns,
            high_null_columns=high_null_columns,
        )


def _numeric_stats(
    vals: list[float], null_count: int, null_fraction: float
) -> NumericColumnStats:
    """Build NumericColumnStats from the raw values collected by a _NumericAcc."""
    n = len(vals)
    if n == 0:
        return NumericColumnStats(null_count=null_count, null_fraction=null_fraction)

    sorted_vals = sorted(vals)
    mean = sum(vals) / n
    variance = sum((v - mean) ** 2 for v in vals) / n
    return NumericColumnStats(
        null_count=null_count,
        null_fraction=null_fraction,
        mean=mean,
        std=math.sqrt(variance),
        min=sorted_vals[0],
        max=sorted_vals[-1],
        percentiles=PercentileStats(
            p5=_percentile(sorted_vals, 5),
            p25=_percentile(sorted_vals, 25),
            p50=_percentile(sorted_vals, 50),
            p75=_percentile(sorted_vals, 75),
            p95=_percentile(sorted_vals, 95),
            p99=_percentile(sorted_vals, 99),
        ),
        histogram=_histogram(sorted_vals, _NUM_HISTOGRAM_BINS),
        num_unique=len(set(vals)),
    )


def _percentile(sorted_vals: list[float], p: int) -> float:
    """Linearly interpolated p-th percentile (0-100) of already-sorted values."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    idx = (p / 100) * (n - 1)
    lo = int(idx)
    hi = lo + 1
    if hi >= n:
        return sorted_vals[-1]
    return sorted_vals[lo] + (idx - lo) * (sorted_vals[hi] - sorted_vals[lo])


def _histogram(sorted_vals: list[float], num_bins: int) -> HistogramStats:
    """Bucket already-sorted values into num_bins equal-width bins."""
    lo, hi = sorted_vals[0], sorted_vals[-1]
    if lo == hi:
        return HistogramStats(bin_edges=[lo, lo + 1.0], counts=[len(sorted_vals)])
    step = (hi - lo) / num_bins
    edges = [lo + i * step for i in range(num_bins + 1)]
    counts = [0] * num_bins
    for v in sorted_vals:
        idx = min(int((v - lo) / step), num_bins - 1)
        counts[idx] += 1
    return HistogramStats(bin_edges=edges, counts=counts)


def _string_stats(acc: _StringAcc, null_fraction: float) -> StringColumnStats:
    """Build StringColumnStats from a fully-populated _StringAcc."""
    n = acc.non_null_count
    top_k = acc.counter.most_common(_TOP_VALUES_K)
    top_values_coverage = sum(c for _, c in top_k) / n if n > 0 else 0.0
    return StringColumnStats(
        null_count=acc.null_count,
        null_fraction=null_fraction,
        num_unique=len(acc.counter),
        top_values=dict(top_k),
        top_values_coverage=top_values_coverage,
        avg_char_length=acc.char_len_sum / n if n > 0 else 0.0,
        min_char_length=acc.char_len_min if n > 0 else 0.0,
        max_char_length=acc.char_len_max if n > 0 else 0.0,
        avg_token_count=acc.token_count_sum / n if n > 0 else 0.0,
        min_token_count=acc.token_count_min if n > 0 else 0.0,
        max_token_count=acc.token_count_max if n > 0 else 0.0,
    )


def _datetime_stats(acc: _DatetimeAcc, null_fraction: float) -> DatetimeColumnStats:
    """Build DatetimeColumnStats from a fully-populated _DatetimeAcc."""
    if acc.non_null_count == 0:
        return DatetimeColumnStats(
            null_count=acc.null_count, null_fraction=null_fraction
        )

    from datetime import datetime as _dt

    range_days = 0.0
    try:
        min_dt = _dt.fromisoformat(acc.min_val.replace("Z", "+00:00"))
        max_dt = _dt.fromisoformat(acc.max_val.replace("Z", "+00:00"))
        range_days = (max_dt - min_dt).total_seconds() / 86400
    except ValueError:
        pass

    return DatetimeColumnStats(
        null_count=acc.null_count,
        null_fraction=null_fraction,
        min=acc.min_val,
        max=acc.max_val,
        range_days=range_days,
    )
