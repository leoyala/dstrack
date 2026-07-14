from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeAlias, runtime_checkable

# A single cell value after dtype coercion; None represents a missing value.
Cell: TypeAlias = int | float | str | bool | None


@dataclass(frozen=True)
class ColumnInfo:
    """Metadata for one column of a tabular dataset.

    Attributes:
        name: Column name as it appears in the source.
        dtype: Storage type using snapshot-schema vocabulary: ``int64``,
            ``float64``, ``string``, ``bool``, ``datetime64``, or ``bytes``.
        nullable: ``True`` if any value in this column may be ``None``.
    """

    name: str
    dtype: str
    nullable: bool = True


@runtime_checkable
class TabularReader(Protocol):
    """Structural protocol for tabular data sources.

    Any class that exposes ``columns()`` and ``iter_batches()`` satisfies this
    protocol, no inheritance required.  Third-party readers for Parquet, SQL,
    HuggingFace datasets, etc. only need to implement these two methods.

    Examples:
        >>> class MyParquetReader:
        ...     def columns(self):
        ...         return [ColumnInfo("x", "int64")]
        ...
        ...     def iter_batches(self, batch_size=1000):
        ...         return iter([[]])
        >>> isinstance(MyParquetReader(), TabularReader)
        True
    """

    def columns(self) -> list[ColumnInfo]:
        """Return column descriptors.

        May open or inspect the source on the first call; subsequent calls
        should return a cached result.

        Returns:
            Ordered list of [ColumnInfo][dstrack.readers.ColumnInfo] objects, one per column.
        """
        ...

    def iter_batches(self, batch_size: int = 1000) -> Iterator[list[list[Cell]]]:
        """Yield non-empty batches of rows.

        Each row is a list of coerced values aligned with ``columns()``.
        Missing values are represented as ``None``.

        Args:
            batch_size: Maximum number of rows per batch.

        Yields:
            A list of rows, each row being a list of [Cell][dstrack.readers.Cell] values.
        """
        ...


@runtime_checkable
class ReaderFactory(Protocol):
    """Construction contract for readers reached *by name* rather than by instance.

    [TabularReader][dstrack.readers.TabularReader] describes how a reader is
    *read*, and says nothing about how one is *built*: code that already holds an
    instance never needs to know. But the registry and the
    ``"package.module:ClassName"`` spec only ever yield a class, so they need a
    uniform way to turn that class into an instance given a source path.
    ``from_path`` is that way, and it is checked against this protocol before the
    class is ever called.

    This is deliberately a second, separate protocol: a reader used only from
    Python (constructed by the caller, handed straight to ``SnapshotBuilder``)
    still needs nothing beyond ``TabularReader``. Only readers that are
    registered, or named on the command line, must also satisfy this one.

    Examples:
        ```python
        >>> from pathlib import Path
        >>> from dstrack.readers import CsvReader, ReaderFactory
        >>> isinstance(CsvReader, ReaderFactory)  # the class, not an instance
        True

        ```
    """

    def from_path(self, path: Path) -> TabularReader:
        """Build a reader for ``path`` using default options.

        Implemented as a ``classmethod`` on the reader class; the protocol is
        therefore checked against the class object itself
        (``isinstance(MyReader, ReaderFactory)``).

        Args:
            path: Path to the dataset source the reader will read.

        Returns:
            An instance satisfying
            [TabularReader][dstrack.readers.TabularReader].
        """
        ...
