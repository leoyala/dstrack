"""A printable report of a snapshot comparison.

The terminal renderer and this one answer different questions. On screen a diff
is read once, by the person who ran the command, who already knows what they
compared. A report is read later and elsewhere, often by someone who did not
run anything, so it carries the context the terminal leaves implicit: what was
compared, when, by whom, and what the thresholds were that called a change
significant.

`reportlab` is imported inside
[write_pdf_report][dstrack._diff_report.write_pdf_report] rather than at module
scope. It is an optional dependency, and importing it eagerly would make the
whole module unimportable wherever the ``report`` extra is not installed --
which is most places, since most runs never write a report.

The counterpart of the terminal renderer's `Text` rule applies here.
`Paragraph` parses a small HTML dialect, so a column named ``a<b>`` would end
the build with a parse error rather than printing. Every user-derived string
therefore reaches a paragraph through [_p][dstrack._diff_report._p], which
escapes unconditionally.
"""

import io
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from xml.sax.saxutils import escape

from dstrack import __version__
from dstrack.diff import (
    ColumnKind,
    ColumnStatDiff,
    MetricDiff,
    SetChange,
    Severity,
    ShiftMethod,
    SnapshotDiff,
)
from dstrack.errors import MissingDependencyError

_LOGO_RESOURCE: Final = "assets/dstrack-logo.png"

# The brand palette, read off docs/assets/dstrack-logo.svg.
_PURPLE_LIGHT: Final = "#a99cff"
_PURPLE: Final = "#7060e0"
_PURPLE_DARK: Final = "#4a3cc0"
_INK: Final = "#1d1568"
_MUTED: Final = "#6b7280"
_ROW_TINT: Final = "#f5f4ff"
_RULE: Final = "#dedcf5"
_OK: Final = "#2e7d32"
_WARN: Final = "#b26a00"
_BAD: Final = "#c62828"
_PAPER: Final = "#ffffff"

_SEVERITY_COLORS: Final[dict[Severity, str]] = {
    Severity.UNCHANGED: _MUTED,
    Severity.INFO: _PURPLE,
    Severity.WARNING: _WARN,
    Severity.CRITICAL: _BAD,
}

_MISSING: Final = "—"
_ARROW: Final = "→"
_SHORT_ID_LEN: Final = 8
_HASH_LEN: Final = 16
_MAX_LISTED_CATEGORIES: Final = 5

_KIND_TITLES: Final[dict[ColumnKind, str]] = {
    ColumnKind.NUMERIC: "Numeric columns",
    ColumnKind.STRING: "Text columns",
    ColumnKind.DATETIME: "Datetime columns",
    ColumnKind.OTHER: "Other columns",
    ColumnKind.UNKNOWN: "Unrecognized columns",
}
_CHANGE_LABELS: Final[tuple[tuple[str, str, str], ...]] = (
    ("added", "Added", _OK),
    ("removed", "Removed", _BAD),
    ("type_changed", "Type changed", _WARN),
    ("nullability_changed", "Nullability", _MUTED),
)
_SHIFT_LABELS: Final[dict[ShiftMethod, str]] = {
    ShiftMethod.PSI: "PSI",
    ShiftMethod.PERCENTILE_SHIFT: "Percentile shift",
    ShiftMethod.TOP_VALUES_TVD: "Total variation",
    ShiftMethod.NONE: "",
}


def write_pdf_report(diff: SnapshotDiff, path: Path, *, now: datetime) -> None:
    """Write a comparison report to `path` as a PDF.

    Args:
        diff: The comparison to report.
        path: Where to write the report. Its parent must exist.
        now: The moment to stamp the report with.

    Raises:
        MissingDependencyError: If `reportlab` is not installed.
        OSError: If `path` cannot be written.
    """
    kit = _import_reportlab()

    document = kit.SimpleDocTemplate(
        str(path),
        pagesize=kit.A4,
        leftMargin=18 * kit.mm,
        rightMargin=18 * kit.mm,
        topMargin=16 * kit.mm,
        bottomMargin=20 * kit.mm,
        title="dstrack dataset diff report",
        author=f"dstrack {__version__}",
        subject=_plain_subject(diff),
    )

    story: list[Any] = []
    story += _banner(kit, diff)
    story += _identity(kit, diff)
    story += _summary(kit, diff)
    story += _schema(kit, diff)
    story += _metrics(kit, diff)
    story += _columns(kit, diff)

    document.build(story, canvasmaker=_numbered_canvas(kit, now=now))


# ---------------------------------------------------------------------------
# The optional dependency
# ---------------------------------------------------------------------------


class _Kit:
    """The reportlab names this module uses, gathered by a single import.

    Passing one object around keeps every builder below free of its own import
    block, and keeps the import itself in one place where its failure can be
    explained rather than raised as an `ImportError` from somewhere deep.

    Attributes:
        colors: The `reportlab.lib.colors` module.
        A4: The A4 page size, in points.
        mm: One millimetre, in points.
        canvas: The `reportlab.pdfgen.canvas` module.
        Image: Flowable for an embedded image.
        KeepTogether: Flowable wrapper that keeps its content on one page.
        Paragraph: Flowable for styled text.
        SimpleDocTemplate: The single-template document builder.
        Spacer: Flowable for vertical whitespace.
        Table: Flowable for a grid of cells.
        TableStyle: Styling commands for a `Table`.
        HRFlowable: Flowable for a horizontal rule.
        title: Style for the report title.
        subtitle: Style for the line under the title.
        heading: Style for section headings.
        subheading: Style for headings within a section.
        body: Style for running text.
        cell: Style for text inside table cells.
        header: Style for table header cells; white, because a paragraph's own
            text color overrides the table's ``TEXTCOLOR``.
        mono: Style for code-like values inside table cells.
        note: Style for muted explanatory text.
    """

    def __init__(self) -> None:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
        from reportlab.platypus import (
            Image,
            KeepTogether,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
        from reportlab.platypus.flowables import HRFlowable

        self.colors = colors
        self.A4 = A4
        self.mm = mm
        self.canvas = canvas
        self.Image = Image
        self.KeepTogether = KeepTogether
        self.Paragraph = Paragraph
        self.SimpleDocTemplate = SimpleDocTemplate
        self.Spacer = Spacer
        self.Table = Table
        self.TableStyle = TableStyle
        self.HRFlowable = HRFlowable

        self.title = ParagraphStyle(
            "dsTitle",
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=colors.HexColor(_PURPLE_DARK),
            alignment=TA_LEFT,
        )
        self.subtitle = ParagraphStyle(
            "dsSubtitle",
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=colors.HexColor(_MUTED),
        )
        self.heading = ParagraphStyle(
            "dsHeading",
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            spaceBefore=10,
            spaceAfter=4,
            textColor=colors.HexColor(_INK),
        )
        self.subheading = ParagraphStyle(
            "dsSubheading",
            fontName="Helvetica-Bold",
            fontSize=9.5,
            leading=12,
            spaceBefore=6,
            spaceAfter=2,
            textColor=colors.HexColor(_PURPLE_DARK),
        )
        self.body = ParagraphStyle(
            "dsBody",
            fontName="Helvetica",
            fontSize=9,
            leading=12.5,
            textColor=colors.HexColor(_INK),
        )
        self.cell = ParagraphStyle(
            "dsCell", parent=self.body, fontSize=8.2, leading=10.5
        )
        self.header = ParagraphStyle(
            "dsHeader", parent=self.cell, textColor=colors.HexColor(_PAPER)
        )
        self.mono = ParagraphStyle(
            "dsMono", parent=self.cell, fontName="Courier", fontSize=7.8
        )
        self.note = ParagraphStyle(
            "dsNote",
            parent=self.body,
            fontSize=8.5,
            textColor=colors.HexColor(_MUTED),
        )


def _import_reportlab() -> _Kit:
    """Load reportlab, explaining how to get it if it is not there.

    Returns:
        The names this module builds a document from.

    Raises:
        MissingDependencyError: If `reportlab` is not installed.
    """
    try:
        return _Kit()
    except ImportError as e:
        raise MissingDependencyError(
            "Writing a PDF report needs `reportlab`, which is not installed.\n"
            "Install it with:  pip install 'dstrack[report]'\n"
            "             or:  uv add 'dstrack[report]'"
        ) from e


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _banner(kit: _Kit, diff: SnapshotDiff) -> list[Any]:
    """Build the report's masthead.

    Args:
        kit: The loaded reportlab names.
        diff: The comparison.

    Returns:
        The logo, title, subtitle and rule.
    """
    heading = [
        kit.Paragraph("Dataset Diff Report", kit.title),
        kit.Paragraph(escape(_plain_subject(diff)), kit.subtitle),
    ]

    logo = _logo(kit)
    masthead: Any
    if logo is None:
        masthead = kit.Table([[heading]], colWidths=[None])
    else:
        masthead = kit.Table(
            [[logo, heading]], colWidths=[20 * kit.mm, None], hAlign="LEFT"
        )
    masthead.setStyle(
        kit.TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return [
        masthead,
        kit.Spacer(1, 5 * kit.mm),
        kit.HRFlowable(
            width="100%", thickness=1.5, color=kit.colors.HexColor(_PURPLE_LIGHT)
        ),
        kit.Spacer(1, 3 * kit.mm),
        _chip(kit, diff.severity.value.upper(), _SEVERITY_COLORS[diff.severity]),
    ]


def _logo(kit: _Kit) -> Any | None:
    """Return the brand logo as a flowable.

    The image is read as bytes rather than as a path. `importlib.resources`
    only guarantees a real file inside an `as_file` block, and the file is gone
    once that block exits -- which would be long before `doc.build` gets round
    to drawing it.

    Args:
        kit: The loaded reportlab names.

    Returns:
        The logo, or ``None`` if it could not be read. A broken install should
        still produce a report: a missing logo is a cosmetic loss, not a reason
        to fail the command.
    """
    from importlib import resources

    try:
        data = resources.files("dstrack").joinpath(_LOGO_RESOURCE).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return None
    return kit.Image(io.BytesIO(data), width=16 * kit.mm, height=16 * kit.mm)


def _identity(kit: _Kit, diff: SnapshotDiff) -> list[Any]:
    """Build the table naming what was compared.

    Args:
        kit: The loaded reportlab names.
        diff: The comparison.

    Returns:
        The section's flowables.
    """
    rows: list[list[Any]] = [_header_row(kit, "Field", "Before", "After")]
    rows.append(
        [
            _p(kit, "Snapshot", kit.cell),
            _p(kit, _short(diff.old.snapshot_id), kit.mono),
            _p(kit, _short(diff.new.snapshot_id), kit.mono),
        ]
    )
    if not diff.same_dataset:
        rows.append(
            [
                _p(kit, "Dataset", kit.cell),
                _p(kit, diff.old.dataset_id or _MISSING, kit.mono),
                _p(kit, diff.new.dataset_id or _MISSING, kit.mono),
            ]
        )
    for label, before, after in (
        ("Name", diff.old.dataset_name, diff.new.dataset_name),
        ("Path", diff.old.dataset_path, diff.new.dataset_path),
        ("Taken", _timestamp(diff.old.created_at), _timestamp(diff.new.created_at)),
        ("By", diff.old.created_by, diff.new.created_by),
    ):
        rows.append(
            [
                _p(kit, label, kit.cell),
                _p(kit, before or _MISSING, kit.cell),
                _p(kit, after or _MISSING, kit.cell),
            ]
        )
    rows.append(
        [
            _p(kit, "Rows", kit.cell),
            _p(kit, _count(diff.old.num_rows), kit.cell),
            _p(kit, _count(diff.new.num_rows), kit.cell),
        ]
    )
    rows.append(
        [
            _p(kit, "Columns", kit.cell),
            _p(kit, _count(diff.old.num_columns), kit.cell),
            _p(kit, _count(diff.new.num_columns), kit.cell),
        ]
    )
    rows.append(
        [
            _p(kit, "Source hash", kit.cell),
            _p(kit, _hash(diff.old.source_hash), kit.mono),
            _p(kit, _hash(diff.new.source_hash), kit.mono),
        ]
    )

    section: list[Any] = [
        kit.Paragraph("What was compared", kit.heading),
    ]
    if not diff.same_dataset:
        section.append(
            _callout(
                kit,
                "These snapshots belong to two different datasets. Column-level "
                "comparisons still apply, but a difference here is not "
                "necessarily drift.",
            )
        )
    section.append(_table(kit, rows, widths=[32 * kit.mm, None, None]))
    return section


def _summary(kit: _Kit, diff: SnapshotDiff) -> list[Any]:
    """Build the plain-language summary of what changed.

    Args:
        kit: The loaded reportlab names.
        diff: The comparison.

    Returns:
        The section's flowables.
    """
    summary = diff.summary
    points: list[str] = []

    if diff.identical:
        points.append("Nothing that is compared differs between the two snapshots.")
    if summary.row_delta:
        points.append(
            f"Row count moved by {summary.row_delta:+,}, "
            f"from {_count(diff.old.num_rows)} to {_count(diff.new.num_rows)}."
        )
    if summary.columns_added:
        points.append(f"{summary.columns_added} column(s) added.")
    if summary.columns_removed:
        points.append(
            f"{summary.columns_removed} column(s) removed, which breaks anything "
            "reading them by name."
        )
    if summary.columns_type_changed:
        points.append(f"{summary.columns_type_changed} column(s) changed type.")
    if summary.columns_drifted:
        points.append(
            f"{summary.columns_drifted} column(s) moved enough statistically to "
            "be worth a look."
        )
    if diff.old.source_hash and diff.new.source_hash:
        points.append(
            "The underlying bytes are identical."
            if diff.source_hash_equal
            else "The underlying file changed."
        )
    if not points:
        points.append("No change reached the configured thresholds.")

    section: list[Any] = [kit.Paragraph("Summary", kit.heading)]
    section += [_p(kit, f"•  {point}", kit.body) for point in points]
    section.append(kit.Spacer(1, 2 * kit.mm))
    section.append(
        _p(
            kit,
            f"Thresholds: a relative change of {diff.options.warn_pct_change:.0%} "
            f"warns and {diff.options.critical_pct_change:.0%} is critical; for "
            "values already expressed as a fraction, "
            f"{diff.options.warn_fraction_shift:.1%} and "
            f"{diff.options.critical_fraction_shift:.1%} absolute. Population "
            f"stability index warns at {diff.options.warn_psi:.2f} and is "
            f"critical at {diff.options.critical_psi:.2f}.",
            kit.note,
        )
    )
    return section


def _schema(kit: _Kit, diff: SnapshotDiff) -> list[Any]:
    """Build the schema-change table.

    Args:
        kit: The loaded reportlab names.
        diff: The comparison.

    Returns:
        The section's flowables.
    """
    section: list[Any] = [kit.Paragraph("Schema changes", kit.heading)]
    schema = diff.schema

    if not schema.changed:
        section.append(_p(kit, "The schema is unchanged.", kit.body))
        if schema.reordered:
            section.append(
                _p(
                    kit,
                    f"{len(schema.reordered)} column(s) appear in a different "
                    "order. The schema hash is order-independent, so this is "
                    "the only place a reorder shows.",
                    kit.note,
                )
            )
        return section

    rows: list[list[Any]] = [_header_row(kit, "Change", "Column", "Before", "After")]
    tints: list[tuple[int, str]] = []
    for attribute, label, color in _CHANGE_LABELS:
        for change in getattr(schema, attribute):
            tints.append((len(rows), color))
            rows.append(
                [
                    _p(kit, label, kit.cell, color=color, bold=True),
                    _p(kit, change.name, kit.cell),
                    _p(kit, _dtype(change.old_dtype, change.old_nullable), kit.cell),
                    _p(kit, _dtype(change.new_dtype, change.new_nullable), kit.cell),
                ]
            )

    section.append(
        _table(kit, rows, widths=[26 * kit.mm, None, 38 * kit.mm, 38 * kit.mm])
    )
    return section


def _metrics(kit: _Kit, diff: SnapshotDiff) -> list[Any]:
    """Build the dataset-level metric table.

    Args:
        kit: The loaded reportlab names.
        diff: The comparison.

    Returns:
        The section's flowables, empty if no dataset metric was recorded.
    """
    if not diff.metrics and not diff.sets:
        return []

    rows: list[list[Any]] = [
        _header_row(kit, "Metric", "Before", "After", "Change", "Relative")
    ]
    for metric in diff.metrics:
        color = _SEVERITY_COLORS[metric.severity]
        rows.append(
            [
                _p(kit, _label(metric.key), kit.cell),
                _p(kit, _value(metric.old), kit.cell),
                _p(kit, _value(metric.new), kit.cell, color=color),
                _p(kit, _delta(metric), kit.cell, color=color),
                _p(
                    kit,
                    f"{metric.pct_change:+.1%}" if metric.pct_change else _MISSING,
                    kit.cell,
                    color=color,
                ),
            ]
        )

    section: list[Any] = [
        kit.Paragraph("Dataset metrics", kit.heading),
        _table(kit, rows, widths=[46 * kit.mm, None, None, None, None]),
    ]
    section.extend(_p(kit, _set_change(change), kit.note) for change in diff.sets)
    return section


def _set_change(change: SetChange) -> str:
    """Describe how a set-valued field's membership moved.

    Args:
        change: The membership change.

    Returns:
        A sentence naming what joined the set and what left it.
    """
    parts = []
    if change.added:
        parts.append(f"joined by {', '.join(change.added)}")
    if change.removed:
        parts.append(f"left by {', '.join(change.removed)}")
    return f"{_label(change.key)}: {', '.join(parts)}"


def _columns(kit: _Kit, diff: SnapshotDiff) -> list[Any]:
    """Build the per-column detail, grouped by statistics variant.

    Args:
        kit: The loaded reportlab names.
        diff: The comparison.

    Returns:
        The section's flowables, empty if no column changed.
    """
    changed = [
        column for column in diff.columns if column.severity is not Severity.UNCHANGED
    ]
    if not changed:
        return []

    # Each heading is bound to the first block beneath it, so a page never ends
    # on a title whose content starts overleaf.
    section: list[Any] = []
    pending: list[Any] = [kit.Paragraph("Column detail", kit.heading)]
    for kind, title in _KIND_TITLES.items():
        group = [column for column in changed if column.kind is kind]
        if not group:
            continue
        pending.append(kit.Paragraph(escape(title), kit.subheading))
        blocks = [_column_block(kit, column) for column in group]
        section.append(kit.KeepTogether([*pending, blocks[0]]))
        section.extend(blocks[1:])
        pending = []
    return section


def _column_block(kit: _Kit, column: ColumnStatDiff) -> Any:
    """Build one column's heading and metric table as an unbreakable unit.

    Args:
        kit: The loaded reportlab names.
        column: The column's comparison.

    Returns:
        A flowable that will not be split across a page boundary, so a
        column's numbers are never separated from the name they belong to.
    """
    heading = _p(
        kit,
        _column_title(column),
        kit.body,
        bold=True,
        color=_SEVERITY_COLORS[column.severity],
    )

    rows: list[list[Any]] = [_header_row(kit, "Statistic", "Before", "After", "Change")]
    for metric in column.metrics:
        if metric.severity is Severity.UNCHANGED:
            continue
        color = _SEVERITY_COLORS[metric.severity]
        rows.append(
            [
                _p(kit, _label(metric.key), kit.cell),
                _p(kit, metric.old_display or _value(metric.old), kit.cell),
                _p(
                    kit,
                    metric.new_display or _value(metric.new),
                    kit.cell,
                    color=color,
                ),
                _p(kit, _delta(metric), kit.cell, color=color),
            ]
        )

    block: list[Any] = [heading]
    if len(rows) > 1:
        block.append(_table(kit, rows, widths=[46 * kit.mm, None, None, None]))
    elif column.present_in_old != column.present_in_new:
        block.append(_p(kit, "Only the column's presence changed.", kit.note))

    # The shift is a property of the pair, not a value either side recorded, so
    # it is stated rather than tabulated: a row of it under Before and After
    # would read as a statistic that went from nothing to 0.23.
    shift = column.distribution_shift
    if shift is not None and shift.score is not None:
        block.append(
            _p(
                kit,
                f"{_SHIFT_LABELS[shift.method]}: {shift.score:.3f}"
                + (f"    {categories}" if (categories := _categories(shift)) else ""),
                kit.note,
                color=_SEVERITY_COLORS[shift.severity],
            )
        )
    block.append(kit.Spacer(1, 2 * kit.mm))
    return kit.KeepTogether(block)


# ---------------------------------------------------------------------------
# Page furniture
# ---------------------------------------------------------------------------


def _numbered_canvas(kit: _Kit, *, now: datetime) -> Any:
    """Build the canvas class that stamps a footer on every page.

    "Page 3 of 7" cannot be drawn while page 3 is being laid out, because the
    total is not known until the last page is done. The pages are therefore
    buffered and their footers drawn at the end, when the count is settled.

    Args:
        kit: The loaded reportlab names.
        now: The moment to stamp the report with.

    Returns:
        A canvas class for `doc.build` to instantiate.
    """
    # The footer says UTC, so an aware moment is converted to it; a naive one
    # is taken to be UTC already and left as it is.
    moment = now.astimezone(UTC) if now.tzinfo is not None else now
    stamp = moment.strftime("%Y-%m-%d %H:%M UTC")

    class _NumberedCanvas(kit.canvas.Canvas):  # type: ignore[misc,name-defined]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._pages: list[dict[str, Any]] = []

        def showPage(self) -> None:
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._footer(total)
                super().showPage()
            super().save()

        def _footer(self, total: int) -> None:
            width, _ = self._pagesize
            self.setStrokeColor(kit.colors.HexColor(_RULE))
            self.setLineWidth(0.5)
            self.line(18 * kit.mm, 14 * kit.mm, width - 18 * kit.mm, 14 * kit.mm)

            self.setFont("Helvetica", 7.5)
            self.setFillColor(kit.colors.HexColor(_MUTED))
            self.drawString(
                18 * kit.mm,
                10 * kit.mm,
                f"dstrack {__version__}  ·  generated {stamp}",
            )
            self.drawRightString(
                width - 18 * kit.mm,
                10 * kit.mm,
                f"Page {self._pageNumber} of {total}",
            )

    return _NumberedCanvas


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def _p(
    kit: _Kit,
    content: str,
    style: Any,
    *,
    bold: bool = False,
    color: str | None = None,
) -> Any:
    """Build a paragraph from text that may contain anything.

    The single place user-derived text becomes a flowable, and so the single
    place the escaping has to be right. `Paragraph` reads its input as markup,
    so a column named ``a<b>`` would abort the build.

    Args:
        kit: The loaded reportlab names.
        content: The text, escaped here rather than by the caller.
        style: The paragraph style to use.
        bold: Render in the bold face.
        color: Override the style's text color.

    Returns:
        The paragraph.
    """
    markup = escape(content)
    if bold:
        markup = f"<b>{markup}</b>"
    if color is not None:
        markup = f'<font color="{color}">{markup}</font>'
    return kit.Paragraph(markup, style)


def _header_row(kit: _Kit, *labels: str) -> list[Any]:
    """Build a table's header row.

    The text is white on the paragraph itself. A table's ``TEXTCOLOR`` command
    does not reach text owned by a `Paragraph`, which would otherwise render in
    the body ink color on the dark header fill.

    Args:
        kit: The loaded reportlab names.
        *labels: The column titles.

    Returns:
        One bold, white paragraph per title.
    """
    return [_p(kit, label, kit.header, bold=True) for label in labels]


def _table(kit: _Kit, rows: Sequence[Sequence[Any]], *, widths: Sequence[Any]) -> Any:
    """Build a table in the report's house style.

    Args:
        kit: The loaded reportlab names.
        rows: The cells, with the header as the first row.
        widths: Column widths, ``None`` for a column that takes what is left.

    Returns:
        The styled table, with its header repeated on every page it spans.
    """
    table = kit.Table(list(rows), colWidths=list(widths), repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), kit.colors.HexColor(_PURPLE_DARK)),
        ("TEXTCOLOR", (0, 0), (-1, 0), kit.colors.HexColor(_PAPER)),
        ("GRID", (0, 0), (-1, -1), 0.25, kit.colors.HexColor(_RULE)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    style += [
        ("BACKGROUND", (0, row), (-1, row), kit.colors.HexColor(_ROW_TINT))
        for row in range(2, len(rows), 2)
    ]
    table.setStyle(kit.TableStyle(style))
    return table


def _chip(kit: _Kit, label: str, color: str) -> Any:
    """Build a small filled badge.

    Args:
        kit: The loaded reportlab names.
        label: The badge's text.
        color: Its fill color.

    Returns:
        A one-cell table standing in for a rounded label.
    """
    chip = kit.Table(
        [[_p(kit, label, kit.cell, bold=True, color=_PAPER)]],
        colWidths=[26 * kit.mm],
        hAlign="LEFT",
    )
    chip.setStyle(
        kit.TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), kit.colors.HexColor(color)),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return chip


def _callout(kit: _Kit, message: str) -> Any:
    """Build a tinted note that should not be read as part of a table.

    Args:
        kit: The loaded reportlab names.
        message: The text to show.

    Returns:
        The callout.
    """
    box = kit.Table([[_p(kit, message, kit.note)]], colWidths=["100%"], hAlign="LEFT")
    box.setStyle(
        kit.TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), kit.colors.HexColor(_ROW_TINT)),
                ("LINEBEFORE", (0, 0), (0, -1), 2, kit.colors.HexColor(_WARN)),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return box


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _plain_subject(diff: SnapshotDiff) -> str:
    """Describe what the report is about, in one line.

    Args:
        diff: The comparison.

    Returns:
        The dataset name or names, and the two short snapshot ids.
    """
    old_name = diff.old.dataset_name or _MISSING
    new_name = diff.new.dataset_name or _MISSING
    subject = old_name if old_name == new_name else f"{old_name} {_ARROW} {new_name}"
    return (
        f"{subject}  ·  {_short(diff.old.snapshot_id)} {_ARROW} "
        f"{_short(diff.new.snapshot_id)}"
    )


def _column_title(column: ColumnStatDiff) -> str:
    """Name a column, saying what happened to it.

    Args:
        column: The column's comparison.

    Returns:
        The name, annotated when the column appeared, disappeared, or changed
        the kind of value it holds.
    """
    if column.old_kind is not None:
        return f"{column.name}  ({column.old_kind.value} {_ARROW} {column.kind.value})"
    if not column.present_in_new:
        return f"{column.name}  (dropped)"
    if not column.present_in_old:
        return f"{column.name}  (new)"
    return column.name


def _categories(shift: Any) -> str:
    """Summarize the categories a text column gained and lost.

    Args:
        shift: The column's distribution shift.

    Returns:
        A short list of each, or an em-dash when nothing came or went.
    """
    parts = []
    for sign, values in (
        ("+", shift.added_categories),
        ("-", shift.removed_categories),
    ):
        if not values:
            continue
        shown = ", ".join(values[:_MAX_LISTED_CATEGORIES])
        if len(values) > _MAX_LISTED_CATEGORIES:
            shown += f", +{len(values) - _MAX_LISTED_CATEGORIES} more"
        label = "gained" if sign == "+" else "lost"
        parts.append(f"{label} {shown}")
    return ";  ".join(parts)


def _label(key: str) -> str:
    """Render a metric key as a human-readable label."""
    _, _, leaf = key.rpartition(".")
    return leaf.replace("_", " ").capitalize()


def _delta(metric: MetricDiff) -> str:
    """Render how far a metric moved.

    Args:
        metric: The comparison.

    Returns:
        The signed change, or an em-dash when there is none to state.
    """
    if metric.delta is None or metric.delta == 0:
        return _MISSING
    return f"{metric.delta:+,.4g}"


def _value(value: float | None) -> str:
    """Format a metric value for a table cell."""
    if value is None:
        return _MISSING
    if float(value).is_integer() and abs(value) < 1e15:
        return f"{int(value):,}"
    return f"{value:,.4g}"


def _count(value: int | None) -> str:
    """Format a recorded count."""
    return _MISSING if value is None else f"{value:,}"


def _dtype(dtype: str | None, nullable: bool | None) -> str:
    """Render a column's type and nullability."""
    if dtype is None and nullable is None:
        return _MISSING
    suffix = "" if nullable is None else (", nullable" if nullable else ", not null")
    return f"{dtype or _MISSING}{suffix}"


def _timestamp(value: str | None) -> str:
    """Render a recorded timestamp for a reader rather than for a machine.

    Args:
        value: The ISO-8601 timestamp, as the snapshot recorded it.

    Returns:
        The moment to the minute, or the raw string if it cannot be parsed --
        an unreadable timestamp is still worth showing, and is not a reason to
        fail a report.
    """
    if not value:
        return _MISSING
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.strftime("%Y-%m-%d %H:%M %Z").strip()


def _short(snapshot_id: str | None) -> str:
    """Return the leading characters of a snapshot id."""
    return _MISSING if snapshot_id is None else snapshot_id[:_SHORT_ID_LEN]


def _hash(value: str | None) -> str:
    """Return a truncated content hash, or an em-dash if there is none."""
    return f"{value[:_HASH_LEN]}…" if value else _MISSING
