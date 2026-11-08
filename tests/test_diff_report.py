"""Tests for the PDF report in src/dstrack/_diff_report.py."""

import base64
import hashlib
import re
import sys
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from dstrack.diff import SnapshotDiff, diff_snapshots
from dstrack.errors import MissingDependencyError

pytest.importorskip("reportlab", reason="the `report` extra is not installed")

from dstrack._diff_report import write_pdf_report

NOW = datetime(2026, 1, 2, 12, 30, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _payload(columns: int = 2, **overrides: Any) -> dict[str, Any]:
    """Build a snapshot payload with `columns` numeric columns."""
    payload: dict[str, Any] = {
        "snapshot_id": "11111111-1111-4111-8111-111111111111",
        "created_at": "2026-01-01T00:00:00+00:00",
        "created_by": "tester",
        "dataset_name": "sales",
        "dataset_path": "data/sales.csv",
        "source_hash": "a" * 64,
        "num_rows": 1000,
        "num_columns": columns,
        "columns": [
            {"name": f"col{i}", "dtype": "float64", "nullable": True}
            for i in range(columns)
        ],
        "column_stats": {
            f"col{i}": {
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
            }
            for i in range(columns)
        },
        "duplicate_row_fraction": 0.0,
        "constant_columns": [],
        "high_null_columns": [],
    }
    payload.update(overrides)
    return payload


def _diff(columns: int = 2, **overrides: Any) -> SnapshotDiff:
    """Build a diff in which every column moved."""
    old = _payload(columns)
    new = _payload(
        columns,
        snapshot_id="22222222-2222-4222-8222-222222222222",
        num_rows=1500,
        source_hash="b" * 64,
        **overrides,
    )
    for stats in new["column_stats"].values():
        stats["mean"] = 40.0
        stats["null_fraction"] = 0.2
    return diff_snapshots(old, new, old_dataset_id="ds", new_dataset_id="ds")


def _pages(pdf: bytes) -> int:
    """Count the pages a PDF declares."""
    return pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages")


def _text(pdf: bytes) -> str:
    """Return the text drawn on a reportlab PDF, one line of it per space.

    Reportlab ASCII85-encodes and deflates its page streams, so the words
    are not visible in the raw bytes. Decoding them here avoids adding a PDF
    parser to the test dependencies. A line can be drawn as several `Tj`
    fragments (an escaped `<` gets one of its own), so fragments are joined
    as they sit and only the `T*` line breaks between them become spaces.
    """
    lines: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.DOTALL):
        data = match.group(1).strip()
        try:
            data = zlib.decompress(base64.a85decode(data.removesuffix(b"~>")))
        except (ValueError, zlib.error):
            continue  # an image or font stream, not page content
        for block in re.findall(rb"BT(.*?)ET", data, re.DOTALL):
            for line in block.split(b"T*"):
                fragments = re.findall(rb"\((.*?)(?<!\\)\)\s*Tj", line)
                if fragments:
                    lines.append(b"".join(fragments).decode("latin-1"))
    return " ".join(lines)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_a_report_is_a_readable_pdf(tmp_path: Path) -> None:
    """The file has to be a PDF before anything else about it matters."""
    path = tmp_path / "diff.pdf"

    write_pdf_report(_diff(), path, now=NOW)

    content = path.read_bytes()
    assert content.startswith(b"%PDF-")
    assert content.endswith(b"%%EOF\n")
    assert len(content) > 1024
    assert _pages(content) >= 1


def test_an_unchanged_comparison_still_produces_a_report(tmp_path: Path) -> None:
    """ "Nothing changed" is a result worth reporting, not a reason to fail."""
    path = tmp_path / "same.pdf"

    write_pdf_report(diff_snapshots(_payload(), _payload()), path, now=NOW)

    assert path.read_bytes().startswith(b"%PDF-")


def test_many_columns_produce_a_multi_page_report(tmp_path: Path) -> None:
    """A wide dataset must paginate rather than overflow one page."""
    path = tmp_path / "wide.pdf"

    write_pdf_report(_diff(columns=30), path, now=NOW)

    assert _pages(path.read_bytes()) > 1


def test_a_cross_dataset_comparison_is_reported(tmp_path: Path) -> None:
    """The report carries the caveat the terminal prints as a warning."""
    across = tmp_path / "across.pdf"
    within = tmp_path / "within.pdf"
    old, new = _payload(), _payload(num_rows=1500)

    write_pdf_report(
        diff_snapshots(old, new, old_dataset_id="a", new_dataset_id="b"),
        across,
        now=NOW,
    )
    write_pdf_report(
        diff_snapshots(old, new, old_dataset_id="a", new_dataset_id="a"),
        within,
        now=NOW,
    )

    assert "two different datasets" in _text(across.read_bytes())
    assert "two different datasets" not in _text(within.read_bytes())


# ---------------------------------------------------------------------------
# Column detail
# ---------------------------------------------------------------------------


def test_a_shift_without_scalar_changes_is_not_called_a_presence_change(
    tmp_path: Path,
) -> None:
    """Histogram counts can move while every tabulated scalar stays put."""
    path = tmp_path / "shift.pdf"
    old, new = _payload(columns=1), _payload(columns=1)
    new["column_stats"]["col0"]["histogram"]["counts"] = [400, 100, 100, 400]

    write_pdf_report(diff_snapshots(old, new), path, now=NOW)

    text = _text(path.read_bytes())
    assert "PSI: " in text
    assert "presence changed" not in text


def test_a_kind_change_is_not_called_a_presence_change(tmp_path: Path) -> None:
    """A column on both sides did not appear or vanish, whatever it now holds."""
    path = tmp_path / "kind.pdf"
    old, new = _payload(columns=1), _payload(columns=1)
    new["column_stats"]["col0"] = {
        "null_count": 0,
        "null_fraction": 0.0,
        "num_unique": 2,
        "top_values": {"a": 600, "b": 400},
        "top_values_coverage": 1.0,
    }

    write_pdf_report(diff_snapshots(old, new), path, now=NOW)

    assert "presence changed" not in _text(path.read_bytes())


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "markup", ["a<b>", "tom & jerry", 'say "what"', "<b>bold</b>", "x</para>"]
)
def test_text_that_looks_like_markup_is_drawn_verbatim(
    tmp_path: Path, markup: str
) -> None:
    """`Paragraph` parses a markup dialect, so every string from the data must
    be escaped: dataset and column names, and the categories a column gained
    and lost, which come straight from the data and are the least trusted.

    Each place gets its own prefix, so one that is drawn correctly cannot
    hide another that is not.
    """
    path = tmp_path / "escaped.pdf"
    dataset, column = f"dataset {markup}", f"column {markup}"
    removed, added = f"removed {markup}", f"added {markup}"
    old = _payload(columns=1, dataset_name=dataset)
    new = _payload(
        columns=1,
        dataset_name=dataset,
        snapshot_id="22222222-2222-4222-8222-222222222222",
    )
    for payload, category, count in ((old, removed, 600), (new, added, 900)):
        payload["columns"][0]["name"] = column
        payload["column_stats"] = {
            column: {
                "null_count": 0,
                "null_fraction": 0.0,
                "num_unique": 2,
                "top_values": {category: count, "plain": 1000 - count},
                "top_values_coverage": 1.0,
            }
        }

    write_pdf_report(diff_snapshots(old, new), path, now=NOW)

    text = _text(path.read_bytes())
    for drawn in (dataset, column, removed, added):
        assert drawn in text, f"{drawn!r} should be drawn verbatim"


# ---------------------------------------------------------------------------
# The optional dependency and the packaged logo
# ---------------------------------------------------------------------------


def test_a_missing_reportlab_explains_how_to_install_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An optional feature must name its extra, not raise an ImportError."""
    monkeypatch.setitem(sys.modules, "reportlab", None)
    monkeypatch.setitem(sys.modules, "reportlab.lib", None)

    with pytest.raises(MissingDependencyError, match=r"dstrack\[report\]"):
        write_pdf_report(_diff(), tmp_path / "nope.pdf", now=NOW)


def test_the_packaged_logo_matches_the_one_in_the_docs() -> None:
    """Two copies of an asset drift; this is the only thing that stops it."""
    packaged = Path("src/dstrack/assets/dstrack-logo.png").read_bytes()
    documented = Path("docs/assets/dstrack-logo.png").read_bytes()

    assert hashlib.sha256(packaged).hexdigest() == (
        hashlib.sha256(documented).hexdigest()
    )


def test_a_report_still_builds_without_its_logo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing asset is a cosmetic loss, not a reason to fail the command."""
    from dstrack import _diff_report

    monkeypatch.setattr(_diff_report, "_LOGO_RESOURCE", "assets/not-there.png")
    path = tmp_path / "nologo.pdf"

    write_pdf_report(_diff(), path, now=NOW)

    assert path.read_bytes().startswith(b"%PDF-")
