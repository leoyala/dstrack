"""Reading a raw snapshot payload without trusting it.

The engine works on two plain mappings, and this is the only layer that touches
them directly. Everything is read with ``.get()`` and checked at the boundary:
a field of the wrong type, or one a payload never carried, degrades to "not
recorded" rather than raising. That is the same stance
[store][dstrack.store] takes when it reads a log line -- a command that
compares two snapshots must not die on a field it was only going to print.

The other job here is deciding which statistics variant a column holds. A
payload is a dict, so the frozen dataclasses
[StatsComputer][dstrack.snapshot.StatsComputer] produced are long gone by the
time the engine sees them, and which arithmetic is valid has to be recovered.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from dstrack.diff._model import ColumnKind, SnapshotRef

# The dtype vocabulary `StatsComputer` dispatches on when it picks an
# accumulator. Kept in step with `dstrack.snapshot._stats._NUMERIC_DTYPES`.
_NUMERIC_DTYPES: Final[frozenset[str]] = frozenset(
    {"int8", "int16", "int32", "int64", "float16", "float32", "float64", "bool"}
)
_STRING_DTYPES: Final[frozenset[str]] = frozenset({"string", "str", "object"})
_DATETIME_DTYPES: Final[frozenset[str]] = frozenset({"datetime64", "datetime"})

# Keys that only one variant of the stats dataclasses ever carries. Checked in
# this order, so a payload carrying several is read as the most specific.
_KIND_MARKERS: Final[tuple[tuple[ColumnKind, frozenset[str]], ...]] = (
    (ColumnKind.STRING, frozenset({"top_values", "avg_char_length"})),
    (ColumnKind.DATETIME, frozenset({"range_days"})),
    (ColumnKind.NUMERIC, frozenset({"histogram", "percentiles", "mean", "std"})),
)


@dataclass(frozen=True, slots=True)
class SchemaEntry:
    """One column as a snapshot's ``columns`` list described it.

    Attributes:
        name: The column's name.
        dtype: Its recorded dtype, or ``None`` if the payload did not say.
        nullable: Whether it was recorded as nullable, or ``None``.
        position: Its index in the ``columns`` list, which is what makes a
            reorder visible.
    """

    name: str
    dtype: str | None
    nullable: bool | None
    position: int


def snapshot_ref(
    payload: Mapping[str, Any], *, dataset_id: str | None = None
) -> SnapshotRef:
    """Read a payload's identity fields.

    Args:
        payload: The snapshot payload.
        dataset_id: Which dataset holds the snapshot. Never present in the
            payload itself, so it is supplied by whoever read it out of a store.

    Returns:
        The identity of that side of the comparison, with anything the payload
        did not record left as ``None``.
    """
    return SnapshotRef(
        snapshot_id=text(payload.get("snapshot_id")),
        dataset_id=dataset_id,
        parent_snapshot_id=text(payload.get("parent_snapshot_id")),
        dataset_name=text(payload.get("dataset_name")),
        dataset_path=text(payload.get("dataset_path")),
        created_at=text(payload.get("created_at")),
        created_by=text(payload.get("created_by")),
        source_type=text(payload.get("source_type")),
        source_hash=text(payload.get("source_hash")),
        schema_hash=text(payload.get("schema_hash")),
        format_version=text(payload.get("format_version")),
        num_rows=integer(payload.get("num_rows")),
        num_columns=integer(payload.get("num_columns")),
    )


def column_schema(payload: Mapping[str, Any]) -> dict[str, SchemaEntry]:
    """Read a payload's ``columns`` list, keyed by column name.

    Args:
        payload: The snapshot payload.

    Returns:
        One entry per named column, in the order the payload listed them. A
        malformed entry, or one with no usable name, is skipped; a name listed
        twice keeps its first position, since that is the one a reorder is
        measured against.
    """
    entries: dict[str, SchemaEntry] = {}
    raw = payload.get("columns")
    if not isinstance(raw, list):
        return entries

    for position, item in enumerate(raw):
        if not isinstance(item, Mapping):
            continue
        name = text(item.get("name"))
        if name is None or name in entries:
            continue
        nullable = item.get("nullable")
        entries[name] = SchemaEntry(
            name=name,
            dtype=text(item.get("dtype")),
            nullable=nullable if isinstance(nullable, bool) else None,
            position=position,
        )
    return entries


def column_stats(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Read a payload's ``column_stats`` mapping.

    Args:
        payload: The snapshot payload.

    Returns:
        Each column's statistics, keyed by column name. Empty if the payload
        recorded none, or recorded something that is not a mapping.
    """
    raw = payload.get("column_stats")
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(name): stats for name, stats in raw.items() if isinstance(stats, Mapping)
    }


def column_kind(stats: Mapping[str, Any] | None, dtype: str | None) -> ColumnKind:
    """Decide which statistics variant a column's entry holds.

    Two sources are consulted, and they are not equally trusted. The declared
    dtype is a hint; the keys actually present are the evidence. When they
    disagree the keys win, because the stats are what is about to be compared
    arithmetically, and a column declared ``float64`` whose entry carries
    ``top_values`` would otherwise have a dict read as a number.

    Args:
        stats: The column's ``column_stats`` entry, or ``None`` if it has none.
        dtype: The dtype the ``columns`` list declared, or ``None``.

    Returns:
        The variant to compare the column as. ``OTHER`` when only null counts
        are present, and ``UNKNOWN`` when neither source says anything, in
        which case only null counts are compared either way.
    """
    if stats is not None:
        for kind, markers in _KIND_MARKERS:
            if markers & stats.keys():
                return kind

    declared = _kind_from_dtype(dtype)
    if declared is not None:
        return declared

    if stats is not None:
        # An entry carrying nothing but null counts is what `OtherColumnStats`
        # is: a column whose dtype the reader did not recognize.
        return ColumnKind.OTHER
    return ColumnKind.UNKNOWN


def _kind_from_dtype(dtype: str | None) -> ColumnKind | None:
    """Map a declared dtype to a statistics variant.

    Args:
        dtype: The recorded dtype, or ``None``.

    Returns:
        The variant that dtype implies, or ``None`` if there is no dtype to go
        on. An unrecognized dtype maps to ``OTHER``, matching what
        `StatsComputer` does with it.
    """
    if dtype is None:
        return None
    lowered = dtype.lower()
    if lowered in _NUMERIC_DTYPES:
        return ColumnKind.NUMERIC
    if lowered in _STRING_DTYPES:
        return ColumnKind.STRING
    if lowered.startswith("datetime") or lowered in _DATETIME_DTYPES:
        return ColumnKind.DATETIME
    return ColumnKind.OTHER


def number(value: Any) -> float | None:
    """Read a value as a float, or ``None`` if it is not one.

    Args:
        value: The recorded value.

    Returns:
        The value as a float. Booleans are rejected: `bool` is a subclass of
        `int`, and a flag that slipped into a numeric field would otherwise be
        averaged as 0 or 1. Non-finite floats are rejected too, since every
        comparison downstream would propagate the NaN rather than report it.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def integer(value: Any) -> int | None:
    """Read a value as an int, or ``None`` if it is not one.

    Args:
        value: The recorded value.

    Returns:
        The value as an int, or ``None``. Booleans are rejected for the same
        reason [number][dstrack.diff._extract.number] rejects them; a float
        carrying an exact integer is accepted, since JSON does not distinguish
        ``3`` from ``3.0``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def text(value: Any) -> str | None:
    """Read a value as a string, or ``None`` if it is not one.

    Args:
        value: The recorded value.

    Returns:
        The string, or ``None``. A value of another type is *not* coerced: a
        `dataset_name` that came back as a list is a payload that lied, and
        showing ``"[1, 2]"`` as the name would hide that.
    """
    return value if isinstance(value, str) else None


def string_tuple(value: Any) -> tuple[str, ...]:
    """Read a value as a tuple of strings.

    Args:
        value: The recorded value, expected to be a list of names.

    Returns:
        Every string it holds, in order. Empty if it is not a list, so a
        malformed ``constant_columns`` reads as "no constant columns" rather
        than stopping the comparison.
    """
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def counts(value: Any) -> dict[str, float]:
    """Read a value as a mapping of category name to count.

    Args:
        value: The recorded value, expected to be a ``top_values`` mapping.

    Returns:
        Each entry whose key is a string and whose count is a usable number.
        Empty if the value is not a mapping.
    """
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, float] = {}
    for key, raw in value.items():
        parsed = number(raw)
        if isinstance(key, str) and parsed is not None:
            result[key] = parsed
    return result


def float_list(value: Any) -> list[float] | None:
    """Read a value as a list of floats.

    Args:
        value: The recorded value, e.g. a histogram's ``bin_edges``.

    Returns:
        The floats it holds, or ``None`` if it is not a list or any element is
        unusable. Unlike the other readers this is all-or-nothing: a histogram
        missing one edge is not a shorter histogram, it is an unusable one.
    """
    if not isinstance(value, list):
        return None
    parsed: list[float] = []
    for item in value:
        as_float = number(item)
        if as_float is None:
            return None
        parsed.append(as_float)
    return parsed
