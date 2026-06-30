from collections.abc import Iterator
from dataclasses import dataclass
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
