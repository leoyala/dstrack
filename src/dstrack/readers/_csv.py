import csv
import logging
import os
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ._protocol import Cell, ColumnInfo

logger = logging.getLogger(__name__)


_NULL_PATTERNS: frozenset[str] = frozenset(
    {"", "null", "NULL", "Null", "NaN", "nan", "NA", "N/A", "n/a", "None", "none"}
)
_BOOL_TRUE: frozenset[str] = frozenset({"true", "True", "TRUE"})
_BOOL_ALL: frozenset[str] = frozenset(
    {"true", "True", "TRUE", "false", "False", "FALSE"}
)


def _deduplicate_fieldnames(fieldnames: list[str]) -> list[str]:
    """Deduplicates strings in list by appending a counter.

    If the list contains repeated values, an int counter is added
    to the end of the value.

    Names that appear exactly once are treated as reserved: generated suffixes
    will never claim them.  For example, ``["a", "a", "a_1"]`` becomes
    ``["a", "a_2", "a_1"]`` rather than ``["a", "a_1", "a_1_1"]``.

    Args:
        fieldnames: list of strings to be deduplicated

    Returns:
        Deduplicated values
    """
    counts = Counter(fieldnames)
    reserved = {name for name, count in counts.items() if count == 1}
    seen: dict[str, int] = {}
    used: set[str] = set()
    result = []
    for name in fieldnames:
        candidate = name
        suffix = seen.get(name, 1)
        while candidate in used or (candidate != name and candidate in reserved):
            candidate = f"{name}_{suffix}"
            suffix += 1
        seen[name] = suffix
        used.add(candidate)
        result.append(candidate)
    return result


def _looks_like_datetime(value: str) -> bool:
    """Check for a leading ISO-8601 date (YYYY-MM-DD) without importing re.

    Args:
        value: Raw string value from a CSV cell.

    Returns:
        ``True`` if the value starts with a ``YYYY-MM-DD`` pattern.
    """
    return (
        len(value) >= 10
        and value[4] == "-"
        and value[7] == "-"
        and value[:4].isdigit()
        and value[5:7].isdigit()
        and value[8:10].isdigit()
    )


def _infer_dtype(samples: list[str]) -> str:
    """Return the narrowest dtype that fits all non-null sample values.

    Probes in order: ``int64``, ``float64``, ``bool``, ``datetime64``,
    falling back to ``string``.

    Args:
        samples: Raw string values from a single column, including empty
            strings for missing values.

    Returns:
        One of ``"int64"``, ``"float64"``, ``"bool"``, ``"datetime64"``,
        or ``"string"``.
    """
    non_null = [v for v in samples if v not in _NULL_PATTERNS]
    if not non_null:
        return "string"
    if all(v.lstrip("-").isdigit() for v in non_null):
        return "int64"
    try:
        for v in non_null:
            float(v)
        return "float64"
    except ValueError:
        pass
    if all(v in _BOOL_ALL for v in non_null):
        return "bool"
    if all(_looks_like_datetime(v) for v in non_null):
        return "datetime64"
    return "string"


def _coerce(raw: str | None, dtype: str) -> Cell:
    """Cast a raw CSV string to the column's inferred Python type.

    Args:
        raw: Raw string from the CSV cell, or ``None`` if the field is absent.
        dtype: Target dtype string (e.g. ``"int64"``, ``"float64"``).

    Returns:
        The coerced value, or ``None`` for empty or missing inputs.
    """
    if raw is None or raw in _NULL_PATTERNS:
        return None
    if dtype == "int64":
        return int(raw)
    if dtype == "float64":
        return float(raw)
    if dtype == "bool":
        if raw not in _BOOL_ALL:
            raise ValueError(f"Invalid boolean value for bool column: {raw!r}")
        return raw in _BOOL_TRUE
    # string, datetime64, bytes — keep as str
    return raw


class CsvReader:
    """Reads a CSV file using the standard-library ``csv`` module.

    Satisfies [TabularReader][dstrack.readers._protocol.TabularReader] without
    inheriting from it.  Column dtypes are inferred from the first
    ``sample_rows`` data rows; every subsequent call to
    [columns()][dstrack.readers._csv.CsvReader.columns] returns the cached
    result.

    The file's modification time and size are recorded when schema inference
    runs.  [iter_batches()][dstrack.readers._csv.CsvReader.iter_batches] checks
    them again before reading and raises [RuntimeError][] if the file has
    changed, preventing silent schema/data mismatches.

    Note:
        Change detection relies on ``mtime_ns`` and file size reported by the
        OS.  On filesystems with coarse modification-time resolution (FAT32,
        some network or CI mounts), two writes that happen within the same
        clock tick will share the same ``mtime_ns``, so a modification that
        also preserves the file size may go undetected.  If you need
        guaranteed detection in such environments, ensure at least one clock
        tick (≥ 10 ms on most systems) elapses between calling
        [columns()][dstrack.readers._csv.CsvReader.columns] and overwriting the
        file.

    Args:
        path: Path to the CSV file.
        sample_rows: Number of rows to read for dtype inference.
            The values are read and data types for each column are inferred
            from them.
        encoding: File encoding passed to [open][].  Defaults to
            ``"utf-8"``; use ``"cp1252"`` or ``"latin-1"`` for Excel exports.
        rename_duplicates: When ``True``, duplicate header names are made unique
            by appending a counter suffix (e.g. ``col``, ``col_1``, ``col_2``).
            Headers that already appear exactly once in the file are treated as
            reserved: generated suffixes will never overwrite them (e.g.
            ``["a", "a", "a_1"]`` → ``["a", "a_2", "a_1"]``).
            When ``False`` (default), a [ValueError][] is raised instead.
        column_dtypes: Optional mapping of column name to dtype string that
            overrides the inferred dtype for those columns.  Only the listed
            columns are affected; all others are still inferred automatically.
            ``"bytes"`` is not a valid override (see ADR-0002); passing it
            raises [ValueError][].
        **csv_kwargs: Forwarded verbatim to [DictReader][csv.DictReader]
            (e.g. ``delimiter=";"``, ``quotechar="'"``).
    """

    EXTENSIONS = (".csv",)

    @classmethod
    def from_path(cls, path: str | Path) -> "CsvReader":
        """Build a reader for ``path`` with default options.

        Satisfies [ReaderFactory][dstrack.readers._protocol.ReaderFactory], which is how
        the registry and ``--reader`` construct a reader they only know by name.
        Options other than the path are not reachable this way; construct the
        reader directly to set them.

        Args:
            path: Path to the CSV file.

        Returns:
            A [CsvReader][dstrack.readers._csv.CsvReader] bound to ``path``.
        """
        return cls(path)

    def __init__(
        self,
        path: str | Path,
        *,
        sample_rows: int = 200,
        encoding: str = "utf-8",
        rename_duplicates: bool = False,
        column_dtypes: dict[str, str] | None = None,
        **csv_kwargs: Any,
    ) -> None:
        if column_dtypes:
            bad = [name for name, dt in column_dtypes.items() if dt == "bytes"]
            if bad:
                raise ValueError(
                    f"'bytes' dtype is not supported by CsvReader (column(s): {bad}). "
                    "Keep the column as 'string' and decode in application code. "
                    "See ADR-0002."
                )
        self._path = Path(path)
        self._sample_rows = sample_rows
        self._encoding = encoding
        self._rename_duplicates = rename_duplicates
        self._column_dtypes = column_dtypes or {}
        self._csv_kwargs = csv_kwargs
        self._columns: list[ColumnInfo] | None = None
        self._file_stat: tuple[int, int] | None = None

    @property
    def path(self) -> Path:
        """Path to the CSV file this reader is bound to."""
        return self._path

    def columns(self) -> list[ColumnInfo]:
        """Return column descriptors, inferring dtypes on the first call.

        Returns:
            Ordered list of [ColumnInfo][dstrack.readers._protocol.ColumnInfo]
            objects, one per CSV field.
        """
        if self._columns is None:
            self._columns = self._detect_columns()
        return list(self._columns)

    def _detect_columns(self) -> list[ColumnInfo]:
        """Read up to ``sample_rows`` rows and infer a ColumnInfo per field.

        Records ``(mtime_ns, size)`` of the open file handle in
        ``_file_stat`` for later comparison by
        [iter_batches()][dstrack.readers._csv.CsvReader.iter_batches].

        Returns:
            Ordered list of [ColumnInfo][dstrack.readers._protocol.ColumnInfo]
            objects, one per CSV field.
        """
        logger.debug(
            f"Inferring column dtypes for {self._path} from up to {self._sample_rows} sample rows",
        )
        samples: dict[str, list[str]] = {}
        with self._path.open(newline="", encoding=self._encoding) as fh:
            stat = os.fstat(fh.fileno())
            self._file_stat = (stat.st_mtime_ns, stat.st_size)
            reader = csv.DictReader(fh, **self._csv_kwargs)
            fieldnames = reader.fieldnames
            if not fieldnames:
                return []
            if len(set(fieldnames)) != len(fieldnames):
                if not self._rename_duplicates:
                    raise ValueError(
                        "Duplicate CSV headers detected. Pass rename_duplicates=True"
                        " to automatically rename them (e.g. 'col', 'col_1', 'col_2')."
                    )
                fieldnames = _deduplicate_fieldnames(list(fieldnames))
                reader.fieldnames = fieldnames
            for name in fieldnames:
                samples[name] = []
            for i, row in enumerate(reader):
                if i >= self._sample_rows:
                    break
                for name in fieldnames:
                    raw = row.get(name)
                    samples[name].append("" if raw is None else raw)

        columns = [
            ColumnInfo(
                name=name,
                dtype=self._column_dtypes.get(name) or _infer_dtype(vals),
                nullable=any(v in _NULL_PATTERNS for v in vals),
            )
            for name, vals in samples.items()
        ]
        logger.debug(f"Inferred {len(columns)} columns for {self._path}")
        return columns

    def iter_batches(self, batch_size: int = 1000) -> Iterator[list[list[Cell]]]:
        """Yield batches of coerced rows.

        Opens the file once per call.  Before reading, compares the file's
        current ``mtime_ns`` and size against the values recorded during
        schema inference and raises if they differ.

        Args:
            batch_size: Maximum number of rows per batch.

        Yields:
            A list of rows; each row is a list of
                [Cell][dstrack.readers._protocol.Cell] values aligned with
                [columns()][dstrack.readers._csv.CsvReader.columns].

        Raises:
            RuntimeError: If the file was modified since
                [columns()][dstrack.readers._csv.CsvReader.columns] was last
                called.
        """
        cols = self.columns()
        names = [c.name for c in cols]
        dtypes = [c.dtype for c in cols]
        batch: list[list[Cell]] = []

        with self._path.open(newline="", encoding=self._encoding) as fh:
            stat = os.fstat(fh.fileno())
            current = (stat.st_mtime_ns, stat.st_size)
            if self._file_stat is not None and current != self._file_stat:
                raise RuntimeError(
                    f"File changed between schema inference and iteration: {self._path}"
                )
            reader = csv.DictReader(fh, **self._csv_kwargs)
            _ = reader.fieldnames  # consume header row
            reader.fieldnames = names
            for row in reader:
                batch.append(
                    [
                        _coerce(row.get(name), dtype)
                        for name, dtype in zip(names, dtypes, strict=True)
                    ]
                )
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
        if batch:
            yield batch
