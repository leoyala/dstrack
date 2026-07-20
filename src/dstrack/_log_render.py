"""Rendering of a dataset's history as a timeline.

This is the only layer that knows what a dataset's history looks like on
screen. It builds renderables and returns them; printing is the caller's job.

The timeline is a two-column ``Table.grid``: a rail on the left, one glyph per
line, and the snapshot's facts on the right. Keeping one grid row per rendered
line is what holds the rail in step with the text beside it, and it is why the
content column must keep rich's default overflow: a column told not to wrap is
dropped entirely when the grid has to shrink, taking the rail's glyphs with it
and leaving a plausible-looking but railless timeline on narrow terminals.

Every value that reaches a cell comes from the store, which means it comes from
whatever the user passed to `dstrack track --name` or from a path on disk.
Cells are therefore assembled as `Text`, never interpolated into rich's markup:
a dataset named ``sales[/]report`` is not markup to be parsed, and treating it
as such raises rather than prints.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Final

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from dstrack.store import LogEntry

_MISSING: Final = "\N{EM DASH}"
_NODE: Final = "\N{BLACK CIRCLE}"
_RAIL: Final = "\N{BOX DRAWINGS LIGHT VERTICAL}"
_SHORT_ID_LEN: Final = 8

# Largest unit first; each is the number of seconds in one of that unit.
_UNITS: Final[tuple[tuple[str, int], ...]] = (
    ("year", 365 * 24 * 3600),
    ("month", 30 * 24 * 3600),
    ("week", 7 * 24 * 3600),
    ("day", 24 * 3600),
    ("hour", 3600),
    ("minute", 60),
    ("second", 1),
)


def render_history(
    entries: Sequence[LogEntry],
    *,
    parents: Mapping[str, LogEntry],
    head_id: str,
    now: datetime,
    oneline: bool = False,
) -> RenderableType:
    """Render a dataset's snapshots as a timeline.

    Args:
        entries: The snapshots to show, in display order. May be a slice of
            the dataset's full history.
        parents: Each snapshot's predecessor, keyed by ``snapshot_id``, built
            from the *full* history rather than `entries`. Passed separately
            so that a snapshot whose parent was sliced off or reordered still
            shows how it differed from it.
        head_id: Id of the dataset's latest snapshot, marked in the output.
        now: The moment to measure each snapshot's age against.
        oneline: Condense each snapshot to a single line.

    Returns:
        A renderable timeline, newest or oldest first according to the order
        of `entries`.
    """
    grid = Table.grid(padding=(0, 1))
    grid.add_column(no_wrap=True)
    # No overflow or wrap settings: rich's defaults ellipsize each cell to one
    # line, which is what keeps every content line paired with one rail glyph.
    grid.add_column()

    for position, entry in enumerate(entries):
        is_last = position == len(entries) - 1
        parent = parents.get(entry.snapshot_id)
        lines = (
            [_oneline(entry, parent=parent, head_id=head_id, now=now)]
            if oneline
            else _detail_lines(entry, parent=parent, head_id=head_id, now=now)
        )

        for offset, line in enumerate(lines):
            grid.add_row(
                _glyph(entry, head_id=head_id, first=offset == 0, last=is_last), line
            )
        if not oneline and not is_last:
            grid.add_row(Text(_RAIL, style="dim cyan"), "")

    return Group(grid)


def _glyph(entry: LogEntry, *, head_id: str, first: bool, last: bool) -> Text:
    """Return the rail character for one rendered line.

    Args:
        entry: The snapshot the line belongs to.
        head_id: Id of the dataset's latest snapshot.
        first: Whether this is the snapshot's first line, which carries the
            node rather than the rail.
        last: Whether this is the oldest snapshot shown. Its continuation lines
            carry no rail, so the timeline ends at the node instead of trailing
            off below it.

    Returns:
        The styled glyph to place in the rail column.
    """
    if first:
        style = "bold cyan" if entry.snapshot_id == head_id else "cyan"
        return Text(_NODE, style=style)
    return Text(" " if last else _RAIL, style="dim cyan")


def _detail_lines(
    entry: LogEntry, *, parent: LogEntry | None, head_id: str, now: datetime
) -> list[Text]:
    """Render one snapshot as its multi-line timeline entry.

    Args:
        entry: The snapshot to render.
        parent: The snapshot it succeeds, for computing deltas, or ``None``.
        head_id: Id of the dataset's latest snapshot.
        now: The moment to measure the snapshot's age against.

    Returns:
        One `Text` per line, each rendered beside its own rail glyph.
    """
    title = Text()
    title.append(_short_id(entry.snapshot_id), style="bold yellow")
    title.append("  ")
    title.append(entry.dataset_name or _MISSING, style="bold")
    if entry.snapshot_id == head_id:
        title.append("  HEAD", style="bold cyan")

    when = Text()
    when.append(_humanize(entry.created_at, now))
    when.append("  by ", style="dim")
    when.append(entry.created_by or _MISSING)

    counts = Text()
    counts.append_text(
        _count(entry.num_rows, "row", parent.num_rows if parent else None)
    )
    counts.append("   ")
    counts.append_text(
        _count(entry.num_columns, "col", parent.num_columns if parent else None)
    )

    return [title, when, counts, Text(entry.dataset_path or _MISSING, style="dim")]


def _oneline(
    entry: LogEntry, *, parent: LogEntry | None, head_id: str, now: datetime
) -> Text:
    """Render one snapshot as a single timeline line.

    Args:
        entry: The snapshot to render.
        parent: The snapshot it succeeds, for computing deltas, or ``None``.
        head_id: Id of the dataset's latest snapshot.
        now: The moment to measure the snapshot's age against.

    Returns:
        The snapshot's identity, age, name and row count on one line.
    """
    line = Text()
    line.append(_short_id(entry.snapshot_id), style="bold yellow")
    line.append("  ")
    line.append(_humanize(entry.created_at, now), style="dim")
    line.append("  ")
    line.append(entry.dataset_name or _MISSING, style="bold")
    line.append("  ")
    line.append_text(_count(entry.num_rows, "row", parent.num_rows if parent else None))
    if entry.snapshot_id == head_id:
        line.append("  HEAD", style="bold cyan")
    return line


def _count(value: int | None, unit: str, parent: int | None) -> Text:
    """Render a count and how it changed from its parent.

    Args:
        value: The count, or ``None`` if the snapshot did not record one.
        unit: Singular name of what is being counted, e.g. ``"row"``.
        parent: The parent snapshot's count, or ``None`` if there is no parent
            or it recorded none.

    Returns:
        The count, or an em-dash if it is missing, followed by its signed
        change when one can be computed.
    """
    text = Text()
    text.append(_MISSING if value is None else f"{value:,}", style="bold")
    text.append(f" {unit}" if value == 1 else f" {unit}s", style="dim")

    delta = _delta(value, parent)
    if delta is not None:
        text.append(" ")
        text.append(delta[0], style=delta[1])
    return text


def _delta(value: int | None, parent: int | None) -> tuple[str, str] | None:
    """Return a count's signed change and the style to render it in.

    Args:
        value: The snapshot's count.
        parent: The parent snapshot's count.

    Returns:
        The change and its style, or ``None`` when there is nothing to say:
        no parent, an unrecorded count on either side, or no change at all. An
        em-dash marks a missing *value*; a delta that cannot be computed is
        simply not shown, rather than shown as unknown.
    """
    if value is None or parent is None or value == parent:
        return None
    change = value - parent
    return f"{change:+,}", "green" if change > 0 else "red"


def _short_id(snapshot_id: str) -> str:
    """Return the leading characters of a snapshot id, as git shows a commit."""
    return snapshot_id[:_SHORT_ID_LEN]


def _humanize(created_at: str | None, now: datetime) -> str:
    """Render when a snapshot was taken, relative to `now`.

    Args:
        created_at: The recorded timestamp, as it appears in the log.
        now: The moment to measure against.

    Returns:
        An approximate age such as ``"3 days ago"``, or an em-dash if the
        timestamp is missing or unreadable. A snapshot dated in the future
        reads as ``"in 3 days"``: the store travels between machines by git,
        so a teammate's clock can legitimately be ahead of this one.
    """
    when = _parse_created_at(created_at)
    if when is None:
        return _MISSING

    seconds = int((now - when).total_seconds())
    magnitude = abs(seconds)
    if magnitude < 1:
        return "just now"

    for unit, size in _UNITS:
        if magnitude >= size:
            count = magnitude // size
            plural = "" if count == 1 else "s"
            return (
                f"{count} {unit}{plural} ago"
                if seconds > 0
                else f"in {count} {unit}{plural}"
            )
    return "just now"


def _parse_created_at(created_at: str | None) -> datetime | None:
    """Parse a recorded timestamp into an aware datetime.

    Args:
        created_at: The recorded timestamp, as it appears in the log.

    Returns:
        The timestamp, or ``None`` if it is missing or unparsable. A timestamp
        without an offset is read as UTC, which is what the store writes; it
        cannot be left naive, since subtracting it from an aware `now` raises.
    """
    if created_at is None:
        return None
    try:
        parsed = datetime.fromisoformat(created_at)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
