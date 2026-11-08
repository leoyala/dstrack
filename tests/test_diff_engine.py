"""Tests for the diff engine in src/dstrack/diff/."""

import json
from typing import Any

import pytest

from dstrack.diff import (
    ColumnKind,
    Severity,
    ShiftMethod,
    diff_snapshots,
    preset,
)
from dstrack.diff._engine import _redistribute
from dstrack.diff._extract import column_kind
from dstrack.diff._model import DiffOptions
from dstrack.errors import IncompatibleSnapshotError

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _numeric(**overrides: Any) -> dict[str, Any]:
    """Build a numeric column's statistics entry."""
    stats: dict[str, Any] = {
        "null_count": 0,
        "null_fraction": 0.0,
        "mean": 10.0,
        "std": 2.0,
        "min": 0.0,
        "max": 20.0,
        "num_unique": 50,
        "percentiles": {
            "p5": 2.0,
            "p25": 6.0,
            "p50": 10.0,
            "p75": 14.0,
            "p95": 18.0,
            "p99": 19.0,
        },
        "histogram": {
            "bin_edges": [0.0, 5.0, 10.0, 15.0, 20.0],
            "counts": [100, 400, 400, 100],
        },
    }
    stats.update(overrides)
    return stats


def _string(**overrides: Any) -> dict[str, Any]:
    """Build a text column's statistics entry."""
    stats: dict[str, Any] = {
        "null_count": 0,
        "null_fraction": 0.0,
        "num_unique": 3,
        "top_values": {"NY": 500, "LA": 300, "SF": 200},
        "top_values_coverage": 1.0,
        "avg_char_length": 2.0,
        "min_char_length": 2.0,
        "max_char_length": 2.0,
        "avg_token_count": 1.0,
        "min_token_count": 1.0,
        "max_token_count": 1.0,
    }
    stats.update(overrides)
    return stats


def _payload(**overrides: Any) -> dict[str, Any]:
    """Build a minimal but complete snapshot payload."""
    payload: dict[str, Any] = {
        "format_version": "1",
        "snapshot_id": "11111111-1111-4111-8111-111111111111",
        "created_at": "2026-01-01T00:00:00+00:00",
        "created_by": "tester",
        "dataset_name": "sales",
        "dataset_path": "data/sales.csv",
        "source_type": "file",
        "source_hash": "a" * 64,
        "num_rows": 1000,
        "num_columns": 2,
        "columns": [
            {"name": "price", "dtype": "float64", "nullable": True},
            {"name": "city", "dtype": "string", "nullable": True},
        ],
        "schema_hash": "b" * 64,
        "column_stats": {"price": _numeric(), "city": _string()},
        "duplicate_row_fraction": 0.0,
        "constant_columns": [],
        "high_null_columns": [],
    }
    payload.update(overrides)
    return payload


def _mutate(**changes: Any) -> dict[str, Any]:
    """Build a payload that differs from `_payload()` in the given fields."""
    payload = json.loads(json.dumps(_payload()))
    payload.update(changes)
    return payload


# ---------------------------------------------------------------------------
# Identity and the no-change case
# ---------------------------------------------------------------------------


def test_a_snapshot_compared_to_itself_is_identical() -> None:
    """Nothing is reported when both sides hold the same payload."""
    result = diff_snapshots(_payload(), _payload())

    assert result.identical is True
    assert result.severity is Severity.UNCHANGED
    assert not result.schema.changed
    assert result.source_hash_equal is True


def test_empty_source_hashes_do_not_prove_identical_bytes() -> None:
    """Two non-file sources both record "", which says nothing about content."""
    result = diff_snapshots(
        _mutate(source_hash=""), _mutate(source_hash="", num_rows=1200)
    )

    assert result.source_hash_equal is False


def test_dataset_ids_decide_whether_it_is_one_dataset() -> None:
    """Comparing two datasets is surfaced rather than refused."""
    across = diff_snapshots(
        _payload(), _payload(), old_dataset_id="ds-a", new_dataset_id="ds-b"
    )
    within = diff_snapshots(
        _payload(), _payload(), old_dataset_id="ds-a", new_dataset_id="ds-a"
    )

    assert across.same_dataset is False
    assert within.same_dataset is True


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_added_column_is_a_warning() -> None:
    """A new column is worth knowing about, but nothing downstream breaks."""
    new = _mutate(
        columns=[
            {"name": "price", "dtype": "float64", "nullable": True},
            {"name": "city", "dtype": "string", "nullable": True},
            {"name": "tier", "dtype": "string", "nullable": True},
        ],
    )
    new["column_stats"]["tier"] = _string()

    result = diff_snapshots(_payload(), new)

    assert [c.name for c in result.schema.added] == ["tier"]
    assert result.schema.added[0].severity is Severity.WARNING
    assert result.summary.columns_added == 1


def test_removed_column_is_critical() -> None:
    """Dropping a column breaks anything that reads it by name."""
    new = _mutate(columns=[{"name": "price", "dtype": "float64", "nullable": True}])
    del new["column_stats"]["city"]

    result = diff_snapshots(_payload(), new)

    assert [c.name for c in result.schema.removed] == ["city"]
    assert result.severity is Severity.CRITICAL
    assert result.summary.columns_removed == 1


def test_type_change_is_critical() -> None:
    """A column that changed dtype is reported as a retype, not an add and drop."""
    new = _mutate(
        columns=[
            {"name": "price", "dtype": "int64", "nullable": True},
            {"name": "city", "dtype": "string", "nullable": True},
        ]
    )

    result = diff_snapshots(_payload(), new)

    assert [c.name for c in result.schema.type_changed] == ["price"]
    assert result.schema.type_changed[0].severity is Severity.CRITICAL
    assert not result.schema.added
    assert not result.schema.removed


def test_nullability_change_is_information_only() -> None:
    """A column that became nullable is noted without raising the alarm."""
    new = _mutate(
        columns=[
            {"name": "price", "dtype": "float64", "nullable": False},
            {"name": "city", "dtype": "string", "nullable": True},
        ]
    )

    result = diff_snapshots(_payload(), new)

    assert [c.name for c in result.schema.nullability_changed] == ["price"]
    assert result.schema.severity is Severity.INFO


def test_reordered_columns_are_the_only_signal_of_a_reorder() -> None:
    """A pure reorder leaves the schema hash equal, so it is reported separately."""
    new = _mutate(
        columns=[
            {"name": "city", "dtype": "string", "nullable": True},
            {"name": "price", "dtype": "float64", "nullable": True},
        ]
    )

    result = diff_snapshots(_payload(), new)

    assert set(result.schema.reordered) == {"price", "city"}
    assert result.schema.schema_hash_equal is True
    assert result.identical is True


def test_a_rename_reads_as_a_removal_and_an_addition() -> None:
    """No rename is inferred: the engine reports only what it can know."""
    new = _mutate(
        columns=[
            {"name": "cost", "dtype": "float64", "nullable": True},
            {"name": "city", "dtype": "string", "nullable": True},
        ]
    )
    new["column_stats"]["cost"] = new["column_stats"].pop("price")

    result = diff_snapshots(_payload(), new)

    assert [c.name for c in result.schema.added] == ["cost"]
    assert [c.name for c in result.schema.removed] == ["price"]


# ---------------------------------------------------------------------------
# Scalar classification
# ---------------------------------------------------------------------------


def test_a_count_is_judged_by_its_relative_change() -> None:
    """A 50% jump in rows is critical; the delta and ratio are both reported."""
    result = diff_snapshots(_payload(), _mutate(num_rows=1500))

    rows = next(m for m in result.metrics if m.key == "num_rows")
    assert rows.delta == 500
    assert rows.pct_change == pytest.approx(0.5)
    assert rows.severity is Severity.CRITICAL
    assert result.summary.row_delta == 500


def test_growth_from_zero_has_no_relative_scale() -> None:
    """Zero to something is notable and cannot be graded as a ratio."""
    result = diff_snapshots(_mutate(num_rows=0), _mutate(num_rows=10))

    rows = next(m for m in result.metrics if m.key == "num_rows")
    assert rows.pct_change is None
    assert rows.severity is Severity.WARNING


@pytest.mark.parametrize(
    ("null_fraction", "expected"),
    [
        (0.0, Severity.UNCHANGED),
        (0.01, Severity.INFO),
        (0.05, Severity.WARNING),
        (0.20, Severity.CRITICAL),
    ],
)
def test_a_fraction_is_judged_by_its_absolute_shift(
    null_fraction: float, expected: Severity
) -> None:
    """A null fraction moving 0.001 to 0.002 has doubled and means nothing."""
    new = _mutate()
    new["column_stats"]["price"]["null_fraction"] = null_fraction

    result = diff_snapshots(_payload(), new)

    column = next(c for c in result.columns if c.name == "price")
    metric = next(m for m in column.metrics if m.key == "null_fraction")
    assert metric.severity is expected


def test_a_metric_neither_side_recorded_is_not_reported() -> None:
    """Fields in the schema that no builder produces are simply absent."""
    result = diff_snapshots(_payload(), _payload())

    assert not [m for m in result.metrics if m.key == "near_duplicate_estimate"]


def test_a_metric_only_one_side_recorded_is_information() -> None:
    """A field appearing is a change in what was recorded, not in the data."""
    result = diff_snapshots(_payload(), _mutate(near_duplicate_estimate=0.3))

    metric = next(m for m in result.metrics if m.key == "near_duplicate_estimate")
    assert metric.old is None
    assert metric.new == pytest.approx(0.3)
    assert metric.severity is Severity.INFO


# ---------------------------------------------------------------------------
# Set-valued fields
# ---------------------------------------------------------------------------


def test_joining_the_constant_columns_warns_and_leaving_informs() -> None:
    """A feature going dead is actionable; one coming back to life is not."""
    joined = diff_snapshots(_payload(), _mutate(constant_columns=["price"]))
    left = diff_snapshots(_mutate(constant_columns=["price"]), _payload())

    assert joined.sets[0].added == ("price",)
    assert joined.sets[0].severity is Severity.WARNING
    assert left.sets[0].removed == ("price",)
    assert left.sets[0].severity is Severity.INFO


# ---------------------------------------------------------------------------
# Distribution shift
# ---------------------------------------------------------------------------


def test_identical_histograms_score_zero() -> None:
    """A distribution that did not move must not read as movement."""
    result = diff_snapshots(_payload(), _payload())

    column = next(c for c in result.columns if c.name == "price")
    assert column.distribution_shift is not None
    assert column.distribution_shift.method is ShiftMethod.PSI
    assert column.distribution_shift.score == pytest.approx(0.0)
    assert column.distribution_shift.severity is Severity.UNCHANGED


def test_disjoint_histograms_are_critical() -> None:
    """Values that moved to a range they never occupied is the clearest drift."""
    new = _mutate()
    new["column_stats"]["price"]["histogram"] = {
        "bin_edges": [100.0, 105.0, 110.0, 115.0, 120.0],
        "counts": [100, 400, 400, 100],
    }

    result = diff_snapshots(_payload(), new)

    column = next(c for c in result.columns if c.name == "price")
    assert column.distribution_shift is not None
    assert column.distribution_shift.score is not None
    assert column.distribution_shift.score > DiffOptions().critical_psi
    assert column.distribution_shift.severity is Severity.CRITICAL


def test_counts_are_redistributed_onto_a_shared_grid_without_loss() -> None:
    """Re-binning conserves the total, or every proportion after it is wrong."""
    spread = _redistribute(
        [0.0, 5.0, 10.0, 15.0, 20.0],
        [100, 400, 400, 100],
        [0.0, 3.0, 5.0, 10.0, 12.0, 15.0, 20.0],
    )

    assert sum(spread) == pytest.approx(1000.0, abs=1e-9)
    assert len(spread) == 6


@pytest.mark.parametrize(
    ("edges", "counts", "expected"),
    [
        pytest.param(
            [0.0, 1.0, 1.0, 2.0], [1.0, 5.0, 1.0], [1.0, 6.0], id="interior-edge"
        ),
        pytest.param([0.0, 2.0, 2.0], [2.0, 3.0], [1.0, 4.0], id="final-edge"),
    ],
)
def test_a_zero_width_bin_keeps_its_whole_count(
    edges: list[float], counts: list[float], expected: list[float]
) -> None:
    """A bin overlapping nothing by width still lands in the bin holding its edge."""
    assert _redistribute(edges, counts, [0.0, 1.0, 2.0]) == pytest.approx(expected)


def test_histograms_with_different_edges_still_compare() -> None:
    """Each snapshot derives its edges from its own range, so they rarely align."""
    new = _mutate()
    new["column_stats"]["price"]["histogram"] = {
        "bin_edges": [0.0, 6.0, 12.0, 18.0, 24.0],
        "counts": [50, 150, 400, 600],
    }

    result = diff_snapshots(_payload(), new)

    column = next(c for c in result.columns if c.name == "price")
    assert column.distribution_shift is not None
    assert column.distribution_shift.method is ShiftMethod.PSI
    assert column.distribution_shift.score is not None


@pytest.mark.parametrize(
    "histogram",
    [
        pytest.param({"bin_edges": [3.0, 4.0], "counts": [1000]}, id="single-bin"),
        pytest.param("not a mapping", id="not-a-mapping"),
        pytest.param({"bin_edges": ["x", 1.0], "counts": [1]}, id="bad-edge"),
        pytest.param({"bin_edges": [0.0, 1.0, 2.0], "counts": [1]}, id="short-counts"),
        pytest.param({"bin_edges": [2.0, 1.0, 0.0], "counts": [1, 1]}, id="descending"),
    ],
)
def test_an_unusable_histogram_falls_back_to_percentiles(histogram: Any) -> None:
    """A histogram with no comparable shape must not stop the column comparing."""
    old = _mutate()
    new = _mutate()
    for payload in (old, new):
        payload["column_stats"]["price"]["histogram"] = histogram
    new["column_stats"]["price"]["percentiles"]["p50"] = 14.0

    result = diff_snapshots(old, new)

    column = next(c for c in result.columns if c.name == "price")
    assert column.distribution_shift is not None
    assert column.distribution_shift.method is ShiftMethod.PERCENTILE_SHIFT
    assert column.distribution_shift.detail is not None


def test_a_column_with_no_usable_distribution_reports_no_shift() -> None:
    """With neither histogram nor percentiles, no shift score is invented."""
    old = _mutate()
    new = _mutate()
    for payload in (old, new):
        stats = payload["column_stats"]["price"]
        del stats["histogram"]
        stats["percentiles"] = {"p50": "unreadable"}

    result = diff_snapshots(old, new)

    column = next(c for c in result.columns if c.name == "price")
    assert column.distribution_shift is not None
    assert column.distribution_shift.method is ShiftMethod.NONE
    assert column.distribution_shift.score is None


def test_a_missing_row_count_is_noted_on_the_category_shift() -> None:
    """Shares computed without a row count are weaker, and the result says so."""
    old = _mutate(num_rows=None)
    new = _mutate(num_rows=None)
    new["column_stats"]["city"]["top_values"] = {"NY": 100, "LA": 900}

    result = diff_snapshots(old, new)

    column = next(c for c in result.columns if c.name == "city")
    assert column.distribution_shift is not None
    assert "row count unavailable" in (column.distribution_shift.detail or "")


@pytest.mark.parametrize(
    ("stats", "dtype", "expected"),
    [
        ({"null_count": 0}, "string", ColumnKind.STRING),
        ({"null_count": 0}, "datetime64[ns]", ColumnKind.DATETIME),
        ({"null_count": 0}, "category", ColumnKind.OTHER),
        ({"null_count": 0}, None, ColumnKind.OTHER),
        (None, None, ColumnKind.UNKNOWN),
    ],
)
def test_a_column_without_stat_markers_is_classified_by_its_dtype(
    stats: dict[str, Any] | None, dtype: str | None, expected: ColumnKind
) -> None:
    """With no variant-specific keys to go on, the declared dtype decides."""
    assert column_kind(stats, dtype) is expected


def test_a_new_dominant_category_shifts_a_text_column() -> None:
    """Top-value churn is what drift looks like for a text column."""
    new = _mutate()
    new["column_stats"]["city"]["top_values"] = {"MIA": 700, "LA": 300}

    result = diff_snapshots(_payload(), new)

    column = next(c for c in result.columns if c.name == "city")
    assert column.distribution_shift is not None
    assert column.distribution_shift.method is ShiftMethod.TOP_VALUES_TVD
    assert column.distribution_shift.added_categories == ("MIA",)
    assert column.distribution_shift.removed_categories == ("NY", "SF")
    assert column.distribution_shift.severity is Severity.CRITICAL


def test_the_untracked_tail_is_carried_as_its_own_bucket() -> None:
    """Coverage falling means the tail grew, even if the top values held."""
    new = _mutate()
    new["column_stats"]["city"]["top_values"] = {
        "NY": 400,
        "LA": 300,
        "SF": 200,
        **{f"category_{i}": 1 for i in range(47)},
    }
    new["column_stats"]["city"]["num_unique"] = 103
    new["column_stats"]["city"]["top_values_coverage"] = 0.947

    result = diff_snapshots(_payload(), new)

    column = next(c for c in result.columns if c.name == "city")
    assert column.distribution_shift is not None
    assert column.distribution_shift.score is not None
    assert column.distribution_shift.score == pytest.approx(0.1)


@pytest.mark.parametrize(
    ("top_values", "coverage"),
    [
        ({"A": -1000, "B": 1100}, 1.0),
        ({"A": 5000}, 1.0),
        ({"A": 50}, 1.5),
    ],
    ids=["negative-count", "count-exceeds-rows", "coverage-out-of-range"],
)
def test_impossible_top_values_are_unmeasurable_not_scored(
    top_values: dict[str, int], coverage: float
) -> None:
    """A score from counts that cannot exist would read as real drift."""
    old = _mutate()
    old["column_stats"]["city"]["top_values"] = top_values
    old["column_stats"]["city"]["top_values_coverage"] = coverage
    new = _mutate()
    new["column_stats"]["city"]["top_values"] = {"B": 100}

    result = diff_snapshots(old, new)

    column = next(c for c in result.columns if c.name == "city")
    assert column.distribution_shift is not None
    assert column.distribution_shift.method is ShiftMethod.NONE
    assert column.distribution_shift.score is None
    assert column.distribution_shift.severity is Severity.UNCHANGED
    assert column.distribution_shift.detail is not None


# ---------------------------------------------------------------------------
# Column kinds
# ---------------------------------------------------------------------------


def test_a_column_that_changed_kind_is_critical_and_barely_comparable() -> None:
    """Numbers and text share nothing but their null counts."""
    new = _mutate(
        columns=[
            {"name": "price", "dtype": "string", "nullable": True},
            {"name": "city", "dtype": "string", "nullable": True},
        ]
    )
    new["column_stats"]["price"] = _string()

    result = diff_snapshots(_payload(), new)

    column = next(c for c in result.columns if c.name == "price")
    assert column.old_kind is ColumnKind.NUMERIC
    assert column.kind is ColumnKind.STRING
    assert column.severity is Severity.CRITICAL
    assert {m.key for m in column.metrics} == {"null_fraction", "null_count"}
    assert column.distribution_shift is None


def test_shifting_both_datetime_bounds_is_a_change_even_at_equal_duration() -> None:
    """A window that slid forward is not the window that was recorded."""

    def _window(start: str, end: str) -> dict[str, Any]:
        payload = _mutate(
            columns=[{"name": "ts", "dtype": "datetime64", "nullable": True}],
            num_columns=1,
        )
        payload["column_stats"] = {
            "ts": {
                "null_count": 0,
                "null_fraction": 0.0,
                "min": start,
                "max": end,
                "range_days": 365.0,
            }
        }
        return payload

    result = diff_snapshots(
        _window("2024-01-01T00:00:00Z", "2024-12-31T00:00:00Z"),
        _window("2025-01-01T00:00:00Z", "2025-12-31T00:00:00Z"),
    )

    column = next(c for c in result.columns if c.name == "ts")
    by_key = {m.key: m for m in column.metrics}
    assert by_key["range_days"].severity is Severity.UNCHANGED
    assert by_key["min"].severity is not Severity.UNCHANGED
    assert by_key["max"].severity is not Severity.UNCHANGED
    assert column.severity is not Severity.UNCHANGED
    assert result.identical is False


def test_the_recorded_keys_outrank_a_declared_dtype() -> None:
    """Arithmetic follows the statistics present, not the dtype claimed."""
    payload = _mutate(columns=[{"name": "price", "dtype": "float64", "nullable": True}])
    payload["column_stats"] = {"price": _string()}

    result = diff_snapshots(payload, payload)

    column = next(c for c in result.columns if c.name == "price")
    assert column.kind is ColumnKind.STRING


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


def test_presets_change_what_counts_as_significant() -> None:
    """The same movement is graded differently at different sensitivities."""
    new = _mutate(num_rows=1150)

    assert diff_snapshots(_payload(), new, options=preset("low")).severity is (
        Severity.INFO
    )
    assert diff_snapshots(_payload(), new, options=preset("high")).severity is (
        Severity.CRITICAL
    )


def test_an_unknown_preset_names_the_ones_that_exist() -> None:
    """A typo'd sensitivity should say what to type instead."""
    with pytest.raises(KeyError, match="normal"):
        preset("aggressive")


# ---------------------------------------------------------------------------
# Format version and malformed input
# ---------------------------------------------------------------------------


def test_a_foreign_format_version_is_refused() -> None:
    """A payload this dstrack cannot read must not be compared as if it could."""
    with pytest.raises(IncompatibleSnapshotError, match="cannot read"):
        diff_snapshots(_mutate(format_version="99"), _payload())


def test_older_supported_format_versions_stay_comparable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An upgrade must not strand snapshots written in an older format."""
    monkeypatch.setattr(
        "dstrack.diff._engine.SUPPORTED_FORMAT_VERSIONS", frozenset({"1", "2"})
    )

    result = diff_snapshots(_payload(), _mutate(format_version="2"))

    assert result.old.format_version == "1"
    assert result.new.format_version == "2"


def test_a_payload_recording_no_format_version_is_accepted() -> None:
    """Hand-built dicts are a supported input and record no version."""
    result = diff_snapshots({"num_rows": 1}, {"num_rows": 2})

    assert result.summary.row_delta == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"num_rows": "many"},
        {"column_stats": []},
        {"columns": "not a list"},
        {"constant_columns": {"not": "a list"}},
        {"column_stats": {"a": {"mean": None, "histogram": {"bin_edges": "x"}}}},
        {"columns": ["not a mapping", {"name": "a"}, {"name": "a"}]},
        {"column_stats": {"city": {"top_values": "not a mapping"}}},
    ],
)
def test_a_malformed_payload_degrades_rather_than_raises(
    payload: dict[str, Any],
) -> None:
    """A display command must not die on a field it was only going to print."""
    result = diff_snapshots(payload, _payload())

    assert result.severity in tuple(Severity)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_to_dict_is_json_serializable_and_carries_its_version() -> None:
    """The JSON output must survive `json.dumps` with no custom encoder."""
    result = diff_snapshots(_payload(), _mutate(num_rows=1500))

    document = result.to_dict()
    encoded = json.dumps(document)

    assert "diff_format_version" in document
    assert json.loads(encoded)["summary"]["row_delta"] == 500
    assert "severity" in document


def test_to_dict_holds_no_enum_objects() -> None:
    """Enums are flattened to their values, so the result compares to literals."""
    result = diff_snapshots(_payload(), _mutate(num_rows=1500))

    document = result.to_dict()

    assert document["severity"] == "critical"
    assert type(document["severity"]) is str
    assert document["columns"][0]["kind"] == "numeric"
    assert type(document["columns"][0]["kind"]) is str
