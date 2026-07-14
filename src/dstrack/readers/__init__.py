"""Tabular data readers for dstrack.

Built-in readers
----------------
[CsvReader][dstrack.readers.CsvReader] - reads ``.csv`` files; no extra dependencies.

Extending
---------
Implement [TabularReader][dstrack.readers.TabularReader] on any class to create a
custom reader. That alone is enough to build a snapshot from Python:

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

To make a reader reachable *by name* as well - from the CLI, or by extension
inference - it must also satisfy
[ReaderFactory][dstrack.readers._protocol.ReaderFactory] (a ``from_path`` classmethod), and
be registered. There are two ways in, for two different situations:

- **Shipping a reader in a package** others install: declare an entry point in
  the ``dstrack.readers`` group, and set ``EXTENSIONS`` on the class. It is then
  picked up automatically, and ``dstrack track data.parquet`` just works with
  nothing extra typed:

    ```toml
    [project.entry-points."dstrack.readers"]
    parquet = "dstrack_parquet:ParquetReader"
    ```

- **A reader in your own project**, not installed as a plugin: call
  [register_reader][dstrack.readers._registry.register_reader] from Python, or name it on
  the command line as ``--reader "mypackage.readers:ExcelReader"``.
"""

from ._csv import CsvReader
from ._protocol import Cell, ColumnInfo, ReaderFactory, TabularReader
from ._registry import (
    ENTRY_POINT_GROUP,
    available_readers,
    known_extensions,
    register_reader,
)
from ._resolve import load_reader_class, resolve_reader, resolve_reader_class

__all__ = [
    "ENTRY_POINT_GROUP",
    "Cell",
    "ColumnInfo",
    "CsvReader",
    "ReaderFactory",
    "TabularReader",
    "available_readers",
    "known_extensions",
    "load_reader_class",
    "register_reader",
    "resolve_reader",
    "resolve_reader_class",
]
