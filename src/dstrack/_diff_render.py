"""Rendering of a snapshot comparison for the terminal.

This is the only layer that knows what a diff looks like on screen. It builds
renderables and returns them; printing is the caller's job.

Everything is laid out as before-and-after pairs, because that is the question
being asked: not "what does this snapshot say" but "what does it say that the
other one did not". A value that did not move is shown once and dimmed, so the
eye lands on the ones that did, and every changed value is colored by how much
it should worry the reader rather than by which direction it went -- a rising
null fraction is not good news because the number went up.

The layout adapts to the terminal in three bands. Wide terminals get the full
per-column tables side by side; medium ones drop the least informative columns;
narrow ones abandon the tabular form entirely and stack each column as its own
small card, since a side-by-side table squeezed into forty cells is not a
side-by-side table any more.

Every value that reaches a cell is user-derived -- a dataset name, a path, a
column name, a category from the data itself. Cells are therefore assembled as
`Text`, never interpolated into rich's markup: a column named ``price[/]usd``
is not markup to be parsed, and treating it as such raises rather than prints.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Final

from rich import box
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from dstrack._log_render import _humanize
from dstrack.diff import (
    ColumnKind,
    ColumnSchemaChange,
    ColumnStatDiff,
    DistributionShift,
    MetricDiff,
    SetChange,
    Severity,
    ShiftMethod,
    SnapshotDiff,
)

_MISSING: Final = "\N{EM DASH}"
_ARROW: Final = "\N{RIGHTWARDS ARROW}"
_ELLIPSIS: Final = "\N{HORIZONTAL ELLIPSIS}"
_SHORT_ID_LEN: Final = 8
_HASH_LEN: Final = 12

# Layout bands, in cells.
_WIDE_MIN_WIDTH: Final = 140
_SIDE_BY_SIDE_MIN_WIDTH: Final = 100

_MAX_LISTED_COLUMNS: Final = 20
_MAX_LISTED_CATEGORIES: Final = 5

_SEVERITY_STYLES: Final[dict[Severity, str]] = {
    Severity.UNCHANGED: "dim",
    Severity.INFO: "cyan",
    Severity.WARNING: "yellow",
    Severity.CRITICAL: "bold red",
}
_SEVERITY_GLYPHS: Final[dict[Severity, str]] = {
    Severity.UNCHANGED: " ",
    Severity.INFO: "\N{BULLET}",
    Severity.WARNING: "\N{WARNING SIGN}",
    Severity.CRITICAL: "\N{HEAVY EXCLAMATION MARK SYMBOL}",
}

# Metrics where a rise is bad news, so the delta takes its color from the
# severity rather than from its sign.
_QUALITY_METRICS: Final[frozenset[str]] = frozenset(
    {"null_fraction", "null_count", "duplicate_row_fraction"}
)

# Numeric detail columns, most informative first. Trimmed from the end as the
# terminal narrows.
_NUMERIC_PRIORITY: Final[tuple[tuple[str, str], ...]] = (
    ("null_fraction", "nulls"),
    ("mean", "mean"),
    ("std", "std"),
    ("percentiles.p50", "p50"),
    ("min", "min"),
    ("max", "max"),
    ("num_unique", "uniq"),
)
_STRING_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("null_fraction", "nulls"),
    ("num_unique", "uniq"),
    ("top_values_coverage", "coverage"),
    ("avg_char_length", "avg len"),
)
_DATETIME_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("null_fraction", "nulls"),
    ("range_days", "range (d)"),
)
_OTHER_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("null_fraction", "nulls"),
    ("null_count", "null count"),
)

_KIND_TITLES: Final[dict[ColumnKind, str]] = {
    ColumnKind.NUMERIC: "Numeric columns",
    ColumnKind.STRING: "Text columns",
    ColumnKind.DATETIME: "Datetime columns",
    ColumnKind.OTHER: "Other columns",
    ColumnKind.UNKNOWN: "Unrecognized columns",
}
_CHANGE_SIGNS: Final[dict[str, tuple[str, str]]] = {
    "added": ("+", "green"),
    "removed": ("-", "red"),
    "type_changed": ("~", "yellow"),
    "nullability_changed": ("~", "dim"),
}
_SHIFT_LABELS: Final[dict[ShiftMethod, str]] = {
    ShiftMethod.PSI: "PSI",
    ShiftMethod.PERCENTILE_SHIFT: "pctl",
    ShiftMethod.TOP_VALUES_TVD: "TVD",
    ShiftMethod.NONE: "",
}


def render_diff(
    diff: SnapshotDiff,
    *,
    width: int,
    now: datetime,
    oneline: bool = False,
    show_unchanged: bool = False,
) -> RenderableType:
    """Render a snapshot comparison.

    Args:
        diff: The comparison to show.
        width: The terminal width in cells, which decides the layout band.
        now: The moment each snapshot's age is measured against.
        oneline: Condense the whole comparison to a single line.
        show_unchanged: Include columns whose statistics did not move.

    Returns:
        A renderable comparison.
    """
    if oneline:
        return _oneline(diff, now=now)

    sections: list[RenderableType] = [_identity(diff, now=now)]

    identical_note = Text(
        "The two snapshots are identical in everything compared.", "green"
    )
    # Identical snapshots keep the concise output unless there is something
    # extra to say: a recorded column reorder, or details asked for by --all.
    if diff.identical and not diff.schema.reordered and not show_unchanged:
        sections.append(identical_note)
        return Group(*sections)

    sections.append(_schema_section(diff))

    metrics = _metrics_table(diff.metrics, show_unchanged=show_unchanged)
    if metrics is not None:
        sections.extend([Text(), Text("Dataset", style="bold"), metrics])

    sections.extend(_set_sections(diff.sets))
    sections.extend(_column_sections(diff, width=width, show_unchanged=show_unchanged))
    sections.extend([Text(), identical_note if diff.identical else _summary(diff)])
    return Group(*sections)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def _identity(diff: SnapshotDiff, *, now: datetime) -> Panel:
    """Render the two snapshots' identities, side by side.

    Args:
        diff: The comparison.
        now: The moment each snapshot's age is measured against.

    Returns:
        A panel bordered in the diff's overall severity.
    """
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", no_wrap=True)
    # No overflow settings on the value columns: rich's defaults ellipsize
    # them, where a column told not to wrap is dropped whole when the panel has
    # to shrink, taking the comparison with it.
    grid.add_column()
    grid.add_column()

    grid.add_row("", Text("before", style="dim"), Text("after", style="dim"))
    grid.add_row(
        "snapshot",
        Text(_short(diff.old.snapshot_id), style="bold yellow"),
        Text(_short(diff.new.snapshot_id), style="bold yellow"),
    )
    if not diff.same_dataset:
        grid.add_row(
            "dataset",
            Text(diff.old.dataset_id or _MISSING),
            Text(diff.new.dataset_id or _MISSING),
        )
    grid.add_row(
        "name",
        Text(diff.old.dataset_name or _MISSING, style="bold"),
        Text(diff.new.dataset_name or _MISSING, style="bold"),
    )
    grid.add_row(
        "path",
        Text(diff.old.dataset_path or _MISSING, style="dim"),
        Text(diff.new.dataset_path or _MISSING, style="dim"),
    )
    grid.add_row(
        "taken",
        Text(_humanize(diff.old.created_at, now)),
        Text(_humanize(diff.new.created_at, now)),
    )
    grid.add_row(
        "by",
        Text(diff.old.created_by or _MISSING),
        Text(diff.new.created_by or _MISSING),
    )
    grid.add_row(
        "rows",
        Text(_count(diff.old.num_rows), style="bold"),
        Text(_count(diff.new.num_rows), style="bold"),
    )
    grid.add_row(
        "columns",
        Text(_count(diff.old.num_columns), style="bold"),
        Text(_count(diff.new.num_columns), style="bold"),
    )
    grid.add_row(
        "source",
        Text(_hash(diff.old.source_hash), style="dim"),
        _source_verdict(diff),
    )

    body: list[RenderableType] = [grid]
    if not diff.same_dataset:
        body.extend(
            [
                Text(),
                Text(
                    "These snapshots come from two different datasets.",
                    style="yellow",
                ),
            ]
        )

    return Panel(
        Group(*body),
        title=Text("dstrack diff"),
        subtitle=_badge(diff.severity),
        border_style=_SEVERITY_STYLES[diff.severity],
        box=box.ROUNDED,
        expand=False,
    )


def _source_verdict(diff: SnapshotDiff) -> Text:
    """Render the later snapshot's source hash and what it says.

    Args:
        diff: The comparison.

    Returns:
        The hash, followed by whether the underlying bytes are known to match.
    """
    text = Text(_hash(diff.new.source_hash), style="dim")
    if diff.source_hash_equal:
        text.append("  identical bytes", style="dim green")
    elif diff.old.source_hash and diff.new.source_hash:
        text.append("  changed", style="bold red")
    return text


def _badge(severity: Severity) -> Text:
    """Render a severity as a short badge.

    Args:
        severity: The severity to show.

    Returns:
        The severity's name, styled and spaced to read as a label.
    """
    return Text(f" {severity.value.upper()} ", style=_SEVERITY_STYLES[severity])


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def _schema_section(diff: SnapshotDiff) -> RenderableType:
    """Render the schema differences.

    Args:
        diff: The comparison.

    Returns:
        A table of column-level changes, or a note that the schema held still.
    """
    schema = diff.schema
    if not schema.changed:
        lines: list[RenderableType] = [
            Text(),
            Text("Schema", style="bold"),
            Text("  unchanged", style="dim green"),
        ]
        if schema.reordered:
            lines.append(
                Text(
                    f"  {len(schema.reordered)} column(s) reordered "
                    "(schema hash unchanged)",
                    style="dim",
                )
            )
        return Group(*lines)

    table = Table(box=box.SIMPLE_HEAD, padding=(0, 1), show_edge=False)
    table.add_column("", no_wrap=True)
    table.add_column("column")
    table.add_column("before")
    table.add_column("after")

    changes = [
        (kind, change)
        for kind, group in (
            ("added", schema.added),
            ("removed", schema.removed),
            ("type_changed", schema.type_changed),
            ("nullability_changed", schema.nullability_changed),
        )
        for change in group
    ]
    for kind, change in changes[:_MAX_LISTED_COLUMNS]:
        sign, style = _CHANGE_SIGNS[kind]
        table.add_row(
            Text(sign, style=style),
            Text(change.name, style=_SEVERITY_STYLES[change.severity]),
            _dtype(change, old=True),
            _dtype(change, old=False),
        )

    body: list[RenderableType] = [Text(), Text("Schema", style="bold"), table]
    if len(changes) > _MAX_LISTED_COLUMNS:
        hidden = len(changes) - _MAX_LISTED_COLUMNS
        body.append(Text(f"  {_ELLIPSIS} and {hidden} more", style="dim"))
    return Group(*body)


def _dtype(change: ColumnSchemaChange, *, old: bool) -> Text:
    """Render one side of a column's type, with its nullability.

    Args:
        change: The column-level change.
        old: Whether to render the baseline side.

    Returns:
        The dtype, or an em-dash for the side the column was absent from.
    """
    dtype = change.old_dtype if old else change.new_dtype
    nullable = change.old_nullable if old else change.new_nullable
    present = (
        (change.old_dtype is not None or change.old_nullable is not None)
        if old
        else (change.new_dtype is not None or change.new_nullable is not None)
    )
    if dtype is None and not present:
        return Text(_MISSING, style="dim")

    text = Text(dtype or _MISSING)
    if nullable is not None:
        text.append(" null" if nullable else " not null", style="dim")
    return text


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _metrics_table(
    metrics: Sequence[MetricDiff], *, show_unchanged: bool
) -> Table | None:
    """Render a group of scalar comparisons.

    Args:
        metrics: The comparisons to show.
        show_unchanged: Include the ones that did not move.

    Returns:
        A grid of metric, before, after and delta, or ``None`` if nothing is
        worth showing.
    """
    shown = [
        m for m in metrics if show_unchanged or m.severity is not Severity.UNCHANGED
    ]
    if not shown:
        return None

    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)
    grid.add_column(style="dim", no_wrap=True)
    grid.add_column(justify="right")
    grid.add_column(justify="right")
    grid.add_column(justify="right")

    for metric in shown:
        grid.add_row(
            Text(
                _SEVERITY_GLYPHS[metric.severity],
                style=_SEVERITY_STYLES[metric.severity],
            ),
            _label(metric.key),
            Text(_value(metric.old), style="dim"),
            Text(_value(metric.new), style=_SEVERITY_STYLES[metric.severity]),
            _delta(metric),
        )
    return grid


def _delta(metric: MetricDiff) -> Text:
    """Render how far a metric moved.

    Args:
        metric: The comparison.

    Returns:
        The signed change and its relative size. A metric where a rise is bad
        news takes its color from the severity, so that a growing null count is
        never green; every other metric is colored by direction, as
        `dstrack log` colors a row-count delta.
    """
    if metric.delta is None or metric.delta == 0:
        return Text("")

    text = Text(f"{metric.delta:+,.4g}", style=_delta_style(metric))
    if metric.pct_change:
        text.append(f" ({metric.pct_change:+.1%})", style="dim")
    return text


def _delta_style(metric: MetricDiff) -> str:
    """Return the style a metric's delta is shown in.

    Args:
        metric: The comparison.

    Returns:
        A severity style for quality metrics, else green or red by direction.
    """
    if metric.key in _QUALITY_METRICS:
        return _SEVERITY_STYLES[metric.severity]
    return "green" if (metric.delta or 0) > 0 else "red"


# ---------------------------------------------------------------------------
# Sets
# ---------------------------------------------------------------------------


def _set_sections(sets: Sequence[SetChange]) -> list[RenderableType]:
    """Render changes to set-valued dataset fields.

    Args:
        sets: The changed sets, e.g. ``constant_columns``.

    Returns:
        The renderables to append, empty if no set changed. Each set gets its
        name and the columns that joined (``+``) or left (``-``) it.
    """
    if not sets:
        return []

    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)
    grid.add_column(style="dim", no_wrap=True)
    grid.add_column()

    for change in sets:
        members = Text()
        listed = [("+", "green", change.added), ("-", "red", change.removed)]
        for sign, style, names in listed:
            for name in names[:_MAX_LISTED_COLUMNS]:
                if members:
                    members.append(" ")
                members.append(f"{sign}{name}", style=style)
            if len(names) > _MAX_LISTED_COLUMNS:
                hidden = len(names) - _MAX_LISTED_COLUMNS
                members.append(f" {sign}{hidden} more", style="dim")
        grid.add_row(
            Text(
                _SEVERITY_GLYPHS[change.severity],
                style=_SEVERITY_STYLES[change.severity],
            ),
            _label(change.key),
            members,
        )
    return [Text(), Text("Column sets", style="bold"), grid]


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------


def _column_sections(
    diff: SnapshotDiff, *, width: int, show_unchanged: bool
) -> list[RenderableType]:
    """Render the per-column comparisons, grouped by statistics variant.

    One table per variant: the meaningful measurements differ between a numeric
    column and a text one, and a single merged table would be mostly em-dashes.

    Args:
        diff: The comparison.
        width: The terminal width, which decides the layout band.
        show_unchanged: Include columns whose statistics did not move.

    Returns:
        The renderables to append, empty if no column is worth showing.
    """
    shown = [
        column
        for column in diff.columns
        if show_unchanged or column.severity is not Severity.UNCHANGED
    ]
    if not shown:
        return []

    sections: list[RenderableType] = [Text(), Text("Columns", style="bold")]
    if width < _SIDE_BY_SIDE_MIN_WIDTH:
        sections.append(_stacked(shown, show_unchanged=show_unchanged))
        return sections

    hidden: list[tuple[ColumnStatDiff, list[MetricDiff]]] = []
    for kind in _KIND_TITLES:
        group = [column for column in shown if column.kind is kind]
        if group:
            sections.extend(
                [
                    Text(f"  {_KIND_TITLES[kind]}", style="dim"),
                    _column_table(group, kind=kind, width=width),
                ]
            )
            shown_keys = {key for key, _ in _fields_for(kind, width=width)}
            for column in group:
                omitted = [
                    metric
                    for metric in column.metrics
                    if (show_unchanged or metric.severity is not Severity.UNCHANGED)
                    and metric.key not in shown_keys
                ]
                if omitted:
                    hidden.append((column, omitted))

    if hidden:
        sections.extend(_omitted_section(hidden, show_unchanged=show_unchanged))
    return sections


def _omitted_section(
    hidden: Sequence[tuple[ColumnStatDiff, Sequence[MetricDiff]]],
    *,
    show_unchanged: bool = False,
) -> list[RenderableType]:
    """Render measurements that the column tables have no cell for.

    A column can be flagged because of a measurement the table does not carry
    (a high percentile, a token count). Without this the flag has no visible
    cause. Only measurements that moved are listed, unless ``show_unchanged``
    is set, in which case unchanged ones are included too.

    Args:
        hidden: Each affected column with its metrics the table omits.
        show_unchanged: Whether unchanged metrics may be present, which changes
            the heading from "changes" to a plain listing.

    Returns:
        The renderables to append.
    """
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)
    grid.add_column(no_wrap=True)
    grid.add_column(style="dim", no_wrap=True)
    grid.add_column(justify="right")
    grid.add_column(justify="right")

    for column, metrics in hidden:
        for metric in metrics:
            grid.add_row(
                Text(
                    _SEVERITY_GLYPHS[metric.severity],
                    style=_SEVERITY_STYLES[metric.severity],
                ),
                Text(column.name, style="bold"),
                Text(metric.key),
                _pair(metric),
                _delta(metric),
            )
    title = "Other column metrics" if show_unchanged else "Other column changes"
    return [Text(), Text(title, style="bold"), grid]


def _column_table(
    columns: Sequence[ColumnStatDiff], *, kind: ColumnKind, width: int
) -> Table:
    """Render one variant's columns as a before-and-after table.

    Args:
        columns: The columns to show, all of one variant.
        kind: The variant they share.
        width: The terminal width, which decides how many measurements fit.

    Returns:
        A table with one row per column and one cell per measurement, each cell
        holding both sides.
    """
    fields = _fields_for(kind, width=width)

    table = Table(box=box.SIMPLE_HEAD, padding=(0, 1), show_edge=False, expand=False)
    table.add_column("", no_wrap=True)
    table.add_column("column")
    for _, label in fields:
        table.add_column(label, justify="right")
    if kind in (ColumnKind.NUMERIC, ColumnKind.STRING):
        table.add_column("shift")

    for column in columns:
        cells: list[RenderableType] = [
            Text(
                _SEVERITY_GLYPHS[column.severity],
                style=_SEVERITY_STYLES[column.severity],
            ),
            _column_name(column),
        ]
        metrics = {metric.key: metric for metric in column.metrics}
        cells.extend(_pair(metrics.get(key)) for key, _ in fields)
        if kind in (ColumnKind.NUMERIC, ColumnKind.STRING):
            cells.append(_shift(column.distribution_shift))
        table.add_row(*cells)
    return table


def _fields_for(kind: ColumnKind, *, width: int) -> tuple[tuple[str, str], ...]:
    """Return the measurements to show for a variant at a given width.

    Args:
        kind: The statistics variant.
        width: The terminal width in cells.

    Returns:
        Metric key and column label pairs. Numeric columns carry the most
        measurements and are the only ones trimmed, dropping the least
        informative first.
    """
    if kind is ColumnKind.STRING:
        return _STRING_COLUMNS
    if kind is ColumnKind.DATETIME:
        return _DATETIME_COLUMNS
    if kind is not ColumnKind.NUMERIC:
        return _OTHER_COLUMNS
    if width >= _WIDE_MIN_WIDTH:
        return _NUMERIC_PRIORITY
    return _NUMERIC_PRIORITY[:4]


def _column_name(column: ColumnStatDiff) -> Text:
    """Render a column's name, noting a variant change.

    Args:
        column: The column's comparison.

    Returns:
        The name, followed by the variants it moved between when it changed
        kind -- the one case where the statistics are not comparable at all.
    """
    text = Text(column.name, style="bold")
    if column.old_kind is not None:
        text.append(
            f" ({column.old_kind.value} {_ARROW} {column.kind.value})", style="bold red"
        )
    elif not column.present_in_new:
        text.append(" (dropped)", style="red")
    elif not column.present_in_old:
        text.append(" (new)", style="green")
    return text


def _pair(metric: MetricDiff | None) -> Text:
    """Render one measurement as its before-and-after pair.

    Args:
        metric: The comparison, or ``None`` if this column never recorded it.

    Returns:
        Both values with an arrow between them when they differ, or the single
        value dimmed when they do not -- an unchanged number is context, not
        news, and showing it twice would bury the ones that moved.
    """
    if metric is None:
        return Text(_MISSING, style="dim")
    new = metric.new_display or _value(metric.new)
    if metric.severity is Severity.UNCHANGED:
        return Text(new, style="dim")

    text = Text(metric.old_display or _value(metric.old), style="dim")
    text.append(f" {_ARROW} ", style="dim")
    text.append(new, style=_SEVERITY_STYLES[metric.severity])
    return text


def _shift(shift: DistributionShift | None) -> Text:
    """Render a column's distribution shift.

    Args:
        shift: The measured shift, or ``None`` if the variant records none.

    Returns:
        The measure's name and score, followed by the categories that came and
        went for a text column.
    """
    if shift is None or shift.score is None:
        return Text(_MISSING, style="dim")

    text = Text(
        f"{_SHIFT_LABELS[shift.method]} {shift.score:.2f}",
        style=_SEVERITY_STYLES[shift.severity],
    )
    for sign, style, categories in (
        ("+", "green", shift.added_categories),
        ("-", "red", shift.removed_categories),
    ):
        for category in categories[:_MAX_LISTED_CATEGORIES]:
            text.append(" ")
            text.append(f"{sign}{category}", style=style)
        if len(categories) > _MAX_LISTED_CATEGORIES:
            extra = len(categories) - _MAX_LISTED_CATEGORIES
            text.append(f" {sign}{extra} more", style="dim")
    return text


def _stacked(
    columns: Sequence[ColumnStatDiff], *, show_unchanged: bool = False
) -> RenderableType:
    """Render each column as its own small card, for a narrow terminal.

    A side-by-side table squeezed into forty cells stops being one, so below
    the threshold the comparison is turned on its side: one heading per column,
    then one line per measurement that moved.

    Args:
        columns: The columns to show.
        show_unchanged: Include measurements that did not move.

    Returns:
        The stacked cards.
    """
    grid = Table.grid(padding=(0, 1))
    grid.add_column(no_wrap=True)
    grid.add_column(style="dim", no_wrap=True)
    grid.add_column()

    for position, column in enumerate(columns):
        if position:
            grid.add_row("", "", "")
        grid.add_row(
            Text(
                _SEVERITY_GLYPHS[column.severity],
                style=_SEVERITY_STYLES[column.severity],
            ),
            _column_name(column),
            Text(column.kind.value, style="dim"),
        )
        for metric in column.metrics:
            if metric.severity is Severity.UNCHANGED and not show_unchanged:
                continue
            grid.add_row("", _label(metric.key), _pair(metric))
        if column.distribution_shift is not None and (
            column.distribution_shift.score is not None
        ):
            grid.add_row("", Text("shift"), _shift(column.distribution_shift))
    return grid


# ---------------------------------------------------------------------------
# Summary and one-line form
# ---------------------------------------------------------------------------


def _summary(diff: SnapshotDiff) -> Text:
    """Render the headline counts.

    Args:
        diff: The comparison.

    Returns:
        A single line naming what changed, or a note that nothing did.
    """
    summary = diff.summary
    text = Text()
    parts: list[tuple[str, str]] = []
    if summary.row_delta:
        parts.append((f"{summary.row_delta:+,} rows", "bold"))
    if summary.columns_added:
        parts.append((f"+{summary.columns_added} columns", "green"))
    if summary.columns_removed:
        parts.append((f"-{summary.columns_removed} columns", "red"))
    if summary.columns_type_changed:
        parts.append((f"{summary.columns_type_changed} columns retyped", "yellow"))
    if summary.columns_drifted:
        parts.append((f"{summary.columns_drifted} columns drifted", "yellow"))
    for change in diff.sets:
        name = change.key.replace("_", " ")
        if change.added:
            parts.append((f"+{len(change.added)} {name}", "yellow"))
        if change.removed:
            parts.append((f"-{len(change.removed)} {name}", "dim"))

    if not parts:
        return Text("No column changed materially.", style="dim")

    for position, (label, style) in enumerate(parts):
        if position:
            text.append("   ")
        text.append(label, style=style)
    return text


def _oneline(diff: SnapshotDiff, *, now: datetime) -> Text:
    """Render the whole comparison as one line.

    Args:
        diff: The comparison.
        now: Unused, kept so the two render paths take the same arguments.

    Returns:
        The two ids, the dataset, what changed, and the overall severity.
    """
    del now
    line = Text()
    line.append(_short(diff.old.snapshot_id), style="bold yellow")
    line.append(f" {_ARROW} ", style="dim")
    line.append(_short(diff.new.snapshot_id), style="bold yellow")
    line.append("  ")
    line.append(diff.new.dataset_name or _MISSING, style="bold")
    line.append("  ")
    line.append_text(_summary(diff))
    line.append("  ")
    line.append_text(_badge(diff.severity))
    return line


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _label(key: str) -> Text:
    """Render a metric key as a human-readable label.

    Args:
        key: The metric's stable name, possibly dotted.

    Returns:
        The label to show, with the nesting dropped and underscores spaced.
    """
    _, _, leaf = key.rpartition(".")
    return Text(leaf.replace("_", " "))


def _value(value: float | None) -> str:
    """Format a metric value for display.

    Args:
        value: The value, or ``None`` if it was not recorded.

    Returns:
        An em-dash for a missing value; an integer without a decimal point when
        the number is whole, since a row count reading ``1200.0`` is noise.
    """
    if value is None:
        return _MISSING
    if float(value).is_integer() and abs(value) < 1e15:
        return f"{int(value):,}"
    return f"{value:,.4g}"


def _count(value: int | None) -> str:
    """Format a recorded count.

    Args:
        value: The count, or ``None`` if it was not recorded.

    Returns:
        The count with thousands separators, or an em-dash.
    """
    return _MISSING if value is None else f"{value:,}"


def _short(snapshot_id: str | None) -> str:
    """Return the leading characters of a snapshot id, as git shows a commit."""
    return _MISSING if snapshot_id is None else snapshot_id[:_SHORT_ID_LEN]


def _hash(value: str | None) -> str:
    """Return a truncated content hash.

    Args:
        value: The recorded hash, which is empty for a non-file source.

    Returns:
        Its leading characters followed by an ellipsis, or an em-dash when
        there is no hash to show.
    """
    if not value:
        return _MISSING
    return f"{value[:_HASH_LEN]}{_ELLIPSIS}"
