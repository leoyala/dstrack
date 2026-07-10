"""Tests for StatsComputer."""

from collections.abc import Iterator
from dataclasses import fields

import pytest

from dstrack.readers import Cell, ColumnInfo, TabularReader
from dstrack.snapshot import DatasetStats, StatsComputer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reader(cols: list[ColumnInfo], rows: list[list[Cell]]) -> TabularReader:
    class _Stub:
        def columns(self) -> list[ColumnInfo]:
            return cols

        def iter_batches(self, batch_size: int = 1000) -> Iterator[list[list[Cell]]]:
            if rows:
                yield rows

    return _Stub()  # type: ignore[return-value]


def _compute(cols: list[ColumnInfo], rows: list[list[Cell]]) -> DatasetStats:
    return StatsComputer().compute(_reader(cols, rows))


# ---------------------------------------------------------------------------
# num_rows
# ---------------------------------------------------------------------------


def test_num_rows_empty() -> None:
    """num_rows is 0 for a dataset with no rows."""
    stats = _compute([ColumnInfo("x", "int64")], [])
    assert stats.num_rows == 0


def test_num_rows_counted() -> None:
    """num_rows matches the number of rows yielded by the reader."""
    stats = _compute(
        [ColumnInfo("x", "int64")],
        [[1], [2], [3]],
    )
    assert stats.num_rows == 3


# ---------------------------------------------------------------------------
# duplicate_row_fraction
# ---------------------------------------------------------------------------


def test_no_duplicates() -> None:
    """duplicate_row_fraction is 0.0 when all rows are distinct."""
    stats = _compute(
        [ColumnInfo("x", "int64")],
        [[1], [2], [3]],
    )
    assert stats.duplicate_row_fraction == 0.0


def test_all_identical_rows() -> None:
    """duplicate_row_fraction counts all but the first occurrence as duplicates."""
    stats = _compute(
        [ColumnInfo("x", "int64")],
        [[5], [5], [5], [5]],
    )
    # 3 out of 4 rows are duplicates
    assert stats.duplicate_row_fraction == pytest.approx(3 / 4, abs=1e-9)


def test_partial_duplicates() -> None:
    """duplicate_row_fraction reflects duplicates across multi-column rows."""
    stats = _compute(
        [ColumnInfo("a", "int64"), ColumnInfo("b", "string")],
        [[1, "x"], [1, "x"], [2, "y"]],
    )
    assert stats.duplicate_row_fraction == pytest.approx(1 / 3, abs=1e-9)


def test_empty_dataset_duplicate_fraction() -> None:
    """duplicate_row_fraction is 0.0 for a dataset with no rows."""
    stats = _compute([ColumnInfo("x", "int64")], [])
    assert stats.duplicate_row_fraction == 0.0


# ---------------------------------------------------------------------------
# high_null_columns
# ---------------------------------------------------------------------------


def test_high_null_columns_detected() -> None:
    """A column with a high null fraction is listed in high_null_columns."""
    cols = [ColumnInfo("a", "int64"), ColumnInfo("b", "string")]
    rows: list[list[Cell]] = [
        [1, None],
        [2, None],
        [3, None],
        [4, "ok"],
    ]
    stats = _compute(cols, rows)
    assert "b" in stats.high_null_columns
    assert "a" not in stats.high_null_columns


def test_no_high_null_columns() -> None:
    """high_null_columns is empty when no column has a high null fraction."""
    stats = _compute(
        [ColumnInfo("x", "int64")],
        [[1], [2]],
    )
    assert stats.high_null_columns == []


# ---------------------------------------------------------------------------
# constant_columns
# ---------------------------------------------------------------------------


def test_constant_numeric_column() -> None:
    """A numeric column with a single repeated value is flagged as constant."""
    stats = _compute(
        [ColumnInfo("x", "int64"), ColumnInfo("y", "int64")],
        [[7, 1], [7, 2], [7, 3]],
    )
    assert "x" in stats.constant_columns
    assert "y" not in stats.constant_columns


def test_constant_string_column() -> None:
    """A string column with a single repeated value is flagged as constant."""
    stats = _compute(
        [ColumnInfo("s", "string")],
        [["hello"], ["hello"], ["hello"]],
    )
    assert "s" in stats.constant_columns


def test_non_constant_string_column() -> None:
    """A string column with varying values is not flagged as constant."""
    stats = _compute(
        [ColumnInfo("s", "string")],
        [["a"], ["b"]],
    )
    assert "s" not in stats.constant_columns


def test_all_null_column_is_constant() -> None:
    """A column that is entirely null is treated as constant."""
    stats = _compute(
        [ColumnInfo("n", "int64")],
        [[None], [None]],
    )
    assert "n" in stats.constant_columns


# ---------------------------------------------------------------------------
# Numeric column stats
# ---------------------------------------------------------------------------


def test_numeric_basic_stats() -> None:
    """min/max/mean/null_count/null_fraction/num_unique are computed correctly."""
    stats = _compute(
        [ColumnInfo("v", "int64")],
        [[1], [2], [3], [4], [5]],
    )
    s = stats.column_stats["v"]
    assert s.min == 1.0
    assert s.max == 5.0
    assert s.mean == pytest.approx(3.0, abs=1e-9)
    assert s.null_count == 0
    assert s.null_fraction == pytest.approx(0.0, abs=1e-9)
    assert s.num_unique == 5


def test_numeric_null_tracking() -> None:
    """Null values in a numeric column are counted and reflected in null_fraction."""
    stats = _compute(
        [ColumnInfo("v", "float64")],
        [[1.0], [None], [3.0]],
    )
    s = stats.column_stats["v"]
    assert s.null_count == 1
    assert s.null_fraction == pytest.approx(1 / 3, abs=1e-9)


def test_numeric_std_single_value() -> None:
    """std is 0.0 for a column with a single non-null value."""
    stats = _compute(
        [ColumnInfo("v", "int64")],
        [[42]],
    )
    assert stats.column_stats["v"].std == pytest.approx(0.0, abs=1e-9)


def test_numeric_percentiles_present() -> None:
    """All expected percentile fields are populated with plausible values."""
    stats = _compute(
        [ColumnInfo("v", "int64")],
        [[i] for i in range(1, 101)],
    )
    p = stats.column_stats["v"].percentiles
    assert {f.name for f in fields(p)} == {"p5", "p25", "p50", "p75", "p95", "p99"}
    assert p.p50 == pytest.approx(50.0, abs=2)
    assert p.p5 == pytest.approx(5.0, abs=2)
    assert p.p25 == pytest.approx(25.0, abs=2)
    assert p.p75 == pytest.approx(75.0, abs=2)
    assert p.p95 == pytest.approx(95.0, abs=2)
    assert p.p99 == pytest.approx(99.0, abs=2)


def test_numeric_histogram_bin_edge_count() -> None:
    """Histogram bin_edges has one more entry than counts, and counts sum to num_rows."""
    rows = [[i] for i in range(1, 101)]
    stats = _compute(
        [ColumnInfo("v", "int64")],
        rows,
    )
    hist = stats.column_stats["v"].histogram
    counts = hist.counts
    edges = hist.bin_edges
    assert len(edges) == len(counts) + 1
    assert sum(counts) == len(rows)


def test_numeric_all_null_returns_zeros() -> None:
    """An all-null numeric column reports zeroed-out mean/std/num_unique."""
    stats = _compute(
        [ColumnInfo("v", "int64")],
        [[None], [None]],
    )
    s = stats.column_stats["v"]
    assert s.mean == 0.0
    assert s.std == 0.0
    assert s.num_unique == 0


def test_bool_treated_as_numeric() -> None:
    """A bool column is computed using numeric stats with 0/1 min/max."""
    stats = _compute(
        [ColumnInfo("flag", "bool")],
        [[True], [False], [True]],
    )
    s = stats.column_stats["flag"]
    assert s.mean == pytest.approx(2 / 3, abs=1e-9)
    assert s.min == 0.0
    assert s.max == 1.0


# ---------------------------------------------------------------------------
# String column stats
# ---------------------------------------------------------------------------


def test_string_basic_stats() -> None:
    """null_count, num_unique, top_values, and top_values_coverage are correct."""
    stats = _compute(
        [ColumnInfo("s", "string")],
        [["hello"], ["world"], ["hello"]],
    )
    s = stats.column_stats["s"]
    assert s.null_count == 0
    assert s.num_unique == 2
    assert s.top_values == {"hello": 2, "world": 1}
    assert s.top_values_coverage == pytest.approx(1.0, abs=1e-9)


def test_string_char_length_stats() -> None:
    """min/max/avg char length are computed across string values."""
    stats = _compute(
        [ColumnInfo("s", "string")],
        [["a"], ["bb"], ["ccc"]],
    )
    s = stats.column_stats["s"]
    assert s.min_char_length == 1
    assert s.max_char_length == 3
    assert s.avg_char_length == pytest.approx(2.0, abs=1e-9)


def test_string_token_count_stats() -> None:
    """min/max/avg token count are computed by splitting on whitespace."""
    stats = _compute(
        [ColumnInfo("s", "string")],
        [["one"], ["two three"], ["four five six"]],
    )
    s = stats.column_stats["s"]
    assert s.min_token_count == 1
    assert s.max_token_count == 3
    assert s.avg_token_count == pytest.approx(2.0, abs=1e-9)


def test_string_null_tracking() -> None:
    """Null values in a string column are counted and reflected in null_fraction."""
    stats = _compute(
        [ColumnInfo("s", "string")],
        [["a"], [None], [None]],
    )
    s = stats.column_stats["s"]
    assert s.null_count == 2
    assert s.null_fraction == pytest.approx(2 / 3, abs=1e-9)


def test_string_top_values_coverage_partial() -> None:
    """top_values_coverage reflects the fraction covered by the top-N cap when unique values exceed it."""
    cols = [ColumnInfo("s", "string")]
    rows: list[list[Cell]] = [[str(i)] for i in range(200)]
    stats = _compute(cols, rows)
    s = stats.column_stats["s"]
    # 50 top values out of 200 = 0.25 coverage
    assert s.top_values_coverage == pytest.approx(50 / 200, abs=1e-9)


# ---------------------------------------------------------------------------
# Datetime column stats
# ---------------------------------------------------------------------------


def test_datetime_min_max() -> None:
    """min/max are the earliest/latest dates regardless of input order."""
    stats = _compute(
        [ColumnInfo("dt", "datetime64")],
        [["2024-01-01"], ["2024-06-15"], ["2023-12-31"]],
    )
    s = stats.column_stats["dt"]
    assert s.min == "2023-12-31"
    assert s.max == "2024-06-15"


def test_datetime_range_days() -> None:
    """range_days is the day span between the min and max dates."""
    stats = _compute(
        [ColumnInfo("dt", "datetime64")],
        [["2024-01-01"], ["2024-01-11"]],
    )
    s = stats.column_stats["dt"]
    assert s.range_days == pytest.approx(10.0, abs=1e-9)


def test_datetime_null_tracking() -> None:
    """Null values in a datetime column are counted and reflected in null_fraction."""
    stats = _compute(
        [ColumnInfo("dt", "datetime64")],
        [["2024-01-01"], [None]],
    )
    s = stats.column_stats["dt"]
    assert s.null_count == 1
    assert s.null_fraction == pytest.approx(0.5, abs=1e-9)


def test_datetime_all_null() -> None:
    """An all-null datetime column reports empty min/max and zero range_days."""
    stats = _compute(
        [ColumnInfo("dt", "datetime64")],
        [[None], [None]],
    )
    s = stats.column_stats["dt"]
    assert s.min == ""
    assert s.max == ""
    assert s.range_days == pytest.approx(0.0, abs=1e-9)
