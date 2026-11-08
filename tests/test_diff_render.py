"""Tests for the terminal renderer in src/dstrack/_diff_render.py."""

from datetime import UTC, datetime
from typing import Any

import pytest
from rich.console import Console

from dstrack._diff_render import _MAX_LISTED_COLUMNS, render_diff
from dstrack.diff import SnapshotDiff, diff_snapshots

NOW = datetime(2026, 1, 2, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _payload(**overrides: Any) -> dict[str, Any]:
    """Build a small snapshot payload with one numeric and one text column."""
    payload: dict[str, Any] = {
        "snapshot_id": "11111111-1111-4111-8111-111111111111",
        "created_at": "2026-01-01T00:00:00+00:00",
        "created_by": "tester",
        "dataset_name": "sales",
        "dataset_path": "data/sales.csv",
        "source_hash": "a" * 64,
        "num_rows": 1000,
        "num_columns": 2,
        "columns": [
            {"name": "price", "dtype": "float64", "nullable": True},
            {"name": "city", "dtype": "string", "nullable": True},
        ],
        "column_stats": {
            "price": {
                "null_count": 0,
                "null_fraction": 0.0,
                "mean": 10.0,
                "std": 2.0,
                "min": 0.0,
                "max": 20.0,
                "num_unique": 50,
                "percentiles": {"p5": 2.0, "p50": 10.0, "p95": 18.0},
                "histogram": {
                    "bin_edges": [0.0, 5.0, 10.0, 15.0, 20.0],
                    "counts": [100, 400, 400, 100],
                },
            },
            "city": {
                "null_count": 0,
                "null_fraction": 0.0,
                "num_unique": 3,
                "top_values": {"NY": 500, "LA": 300, "SF": 200},
                "top_values_coverage": 1.0,
                "avg_char_length": 2.0,
            },
        },
        "duplicate_row_fraction": 0.0,
        "constant_columns": [],
        "high_null_columns": [],
    }
    payload.update(overrides)
    return payload


def _changed_diff() -> SnapshotDiff:
    """Build a diff with a row change, a null change and a category change."""
    new = _payload(
        snapshot_id="22222222-2222-4222-8222-222222222222",
        num_rows=1500,
        source_hash="b" * 64,
    )
    new["column_stats"]["price"]["null_fraction"] = 0.2
    new["column_stats"]["price"]["mean"] = 40.0
    new["column_stats"]["city"]["top_values"] = {"MIA": 900, "LA": 600}
    return diff_snapshots(_payload(), new, old_dataset_id="ds", new_dataset_id="ds")


def _render(diff: SnapshotDiff, *, width: int = 160, **kwargs: Any) -> str:
    """Render a diff at a given width and return its plain text."""
    console = Console(
        width=width, record=True, force_terminal=False, legacy_windows=False
    )
    console.print(render_diff(diff, width=width, now=NOW, **kwargs))
    return console.export_text()


# ---------------------------------------------------------------------------
# Layout bands
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [160, 120, 60])
def test_the_comparison_renders_at_every_width(width: int) -> None:
    """Wide, medium and narrow terminals must all produce output."""
    output = _render(_changed_diff(), width=width)

    assert "sales" in output
    assert "price" in output
    assert "city" in output


def test_a_narrow_terminal_stacks_each_column_instead_of_tabulating() -> None:
    """A side-by-side table squeezed into forty cells is not one any more."""
    wide = _render(_changed_diff(), width=160)
    narrow = _render(_changed_diff(), width=60)

    # The stacked form spells each statistic out on its own line, so the
    # per-column header row that only the tabular form has is gone.
    assert "null fraction" in narrow
    assert "uniq" in wide
    assert max(len(line) for line in narrow.splitlines()) <= 60


def _numeric_header(output: str) -> str:
    """Return the header row of the numeric column table."""
    lines = output.splitlines()
    start = next(i for i, line in enumerate(lines) if "Numeric columns" in line)
    return lines[start + 1]


def test_a_wide_terminal_shows_more_statistics_than_a_medium_one() -> None:
    """The least informative measurements are dropped first as space runs out."""
    wide = _numeric_header(_render(_changed_diff(), width=160))
    medium = _numeric_header(_render(_changed_diff(), width=120))

    assert "min" in wide
    assert "max" in wide
    assert "uniq" in wide
    assert "min" not in medium
    assert "p50" in medium


# ---------------------------------------------------------------------------
# Markup safety
# ---------------------------------------------------------------------------


def test_names_that_look_like_markup_are_printed_verbatim() -> None:
    """A dataset called `sales[/]report` is not markup, and must not be parsed."""
    old = _payload(dataset_name="sales[/]report")
    new = _payload(
        dataset_name="sales[/]report",
        snapshot_id="22222222-2222-4222-8222-222222222222",
        num_rows=1500,
    )
    for payload in (old, new):
        payload["columns"][0]["name"] = "price[/]usd"
        payload["column_stats"]["price[/]usd"] = payload["column_stats"].pop("price")
    new["column_stats"]["price[/]usd"]["mean"] = 40.0

    output = _render(diff_snapshots(old, new))

    assert "sales[/]report" in output
    assert "price[/]usd" in output


def test_a_category_that_looks_like_markup_is_printed_verbatim() -> None:
    """Categories come straight from the data and are the least trusted of all."""
    new = _payload(snapshot_id="22222222-2222-4222-8222-222222222222", num_rows=1500)
    new["column_stats"]["city"]["top_values"] = {"[bold]NY": 900, "LA": 600}

    output = _render(diff_snapshots(_payload(), new))

    assert "[bold]NY" in output


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


def test_an_unchanged_comparison_says_so_and_shows_no_tables() -> None:
    """Two identical snapshots need one sentence, not an empty table."""
    output = _render(diff_snapshots(_payload(), _payload()))

    assert "identical" in output
    assert "Schema" not in output


def test_an_identical_comparison_keeps_reorder_notes_and_all_details() -> None:
    """The identical shortcut must not swallow a reorder or what `--all` asks for."""
    new = _payload()
    new["columns"].reverse()
    diff = diff_snapshots(_payload(), new)
    assert diff.identical
    assert diff.schema.reordered

    default = _render(diff)
    everything = _render(diff, show_unchanged=True)

    assert "reordered" in default
    assert "identical" in default
    assert "Columns" not in default
    assert "reordered" in everything
    assert "identical" in everything
    assert "Columns" in everything


def test_show_unchanged_includes_a_column_that_did_not_move() -> None:
    """`--all` is for reading the whole picture, not only what changed."""
    old = _payload()
    new = _payload(snapshot_id="22222222-2222-4222-8222-222222222222", num_rows=1500)
    new["column_stats"]["price"]["mean"] = 40.0
    for payload in (old, new):
        payload["columns"].append(
            {"name": "stable", "dtype": "float64", "nullable": True}
        )
        payload["column_stats"]["stable"] = {
            **payload["column_stats"]["price"],
            "mean": 123.5,
        }
    diff = diff_snapshots(old, new)

    default = _render(diff)
    everything = _render(diff, show_unchanged=True)

    assert "stable" not in default
    assert "stable" in everything
    assert "123.5" not in default
    assert "123.5" in everything


def test_oneline_is_a_single_line() -> None:
    """The condensed form has to fit a shell prompt or a CI log line."""
    output = _render(_changed_diff(), oneline=True).strip()

    assert len(output.splitlines()) == 1
    assert "CRITICAL" in output


def test_a_long_list_of_schema_changes_is_capped() -> None:
    """A cross-dataset diff can add hundreds of columns; the table stays readable."""
    total = _MAX_LISTED_COLUMNS + 5
    new = _payload(snapshot_id="22222222-2222-4222-8222-222222222222")
    new["columns"] = [
        {"name": f"col{i}", "dtype": "float64", "nullable": True} for i in range(total)
    ]
    new["column_stats"] = {
        f"col{i}": {"null_count": 0, "null_fraction": 0.0} for i in range(total)
    }

    output = _render(diff_snapshots(_payload(), new))

    assert "and 7 more" in output


def test_comparing_two_datasets_is_called_out(tmp_path: object) -> None:
    """A cross-dataset comparison is legitimate, but the reader should know."""
    diff = diff_snapshots(
        _payload(), _payload(num_rows=1500), old_dataset_id="a", new_dataset_id="b"
    )

    output = _render(diff)

    assert "two different datasets" in output
