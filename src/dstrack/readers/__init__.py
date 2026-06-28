"""Tabular data readers for dstrack.

Built-in readers
----------------
[CsvReader][dstrack.readers.CsvReader] - reads ``.csv`` files; no extra dependencies.

Extending
---------
Implement [TabularReader][dstrack.readers.TabularReader] on any class to create a custom reader.

Examples:
    ```python
    >>> from dstrack.readers import ColumnInfo, TabularReader
    >>> class MyParquetReader:
    ...     def columns(self):
    ...         return [ColumnInfo("x", "int64")]
    ...     def iter_batches(self, batch_size=1000):
    ...         return iter([[]])
    ...
    >>> isinstance(MyParquetReader(), TabularReader)
    True

    ```
"""

from ._csv import CsvReader
from ._protocol import Cell, ColumnInfo, TabularReader

__all__ = ["Cell", "ColumnInfo", "CsvReader", "TabularReader"]
