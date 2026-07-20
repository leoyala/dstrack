"""A SQLite index over the store's dataset logs.

The index at ``.dstrack/.cache/index.db`` answers "what is this dataset's
history?" and "which dataset was recorded at this path?" without reparsing
every dataset's ``log.jsonl`` on every invocation.

It is a cache, never a second source of truth. ``log.jsonl`` is committed to
git and is authoritative; the index is gitignored, derived from it,
and safe to delete at any moment. Two consequences shape this module:

- **It can be stale, so every read resyncs first.** The index does not travel
  with the repository, and `dstrack track` does not write to it, so a clone, a
  pull, or a snapshot taken on another machine all leave it behind. Before any
  query, [sync][dstrack.cache.sync] fingerprints each dataset's log and
  reimports the ones that changed. When nothing changed, that costs one
  ``stat`` and one tiny ``HEAD`` read per dataset.
- **It can be discarded, so damage is repaired rather than reported.** An
  unreadable file or a schema from another version is rebuilt in place; there
  is nothing to lose by doing so.

``HEAD`` is deliberately *not* cached. It is a few bytes, it is the store's
commit pointer, and reading it fresh keeps
[write_snapshot][dstrack.store.write_snapshot]'s central invariant intact: the
index caches parsed lines, while what those lines *mean* is still decided by
the ``HEAD`` on disk.
"""

import contextlib
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from dstrack import store
from dstrack.errors import IndexUnusable
from dstrack.store import DatasetSummary, LogEntry

_log = logging.getLogger(__name__)

CACHE_DIRNAME: Final = ".cache"
INDEX_FILENAME: Final = "index.db"

# Bump whenever the schema below changes: a mismatch rebuilds the index rather
# than querying it with the wrong shape.
_SCHEMA_VERSION: Final = "1"

_SCHEMA: Final = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE datasets (
    dataset_id       TEXT PRIMARY KEY,
    log_size         INTEGER NOT NULL,
    log_mtime_ns     INTEGER NOT NULL,
    head_snapshot_id TEXT
);

CREATE TABLE snapshots (
    snapshot_id        TEXT PRIMARY KEY,
    dataset_id         TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    parent_snapshot_id TEXT,
    created_at         TEXT,
    created_by         TEXT,
    dataset_name       TEXT,
    dataset_path       TEXT,
    num_rows           INTEGER,
    num_columns        INTEGER
);

CREATE INDEX idx_snapshots_dataset ON snapshots(dataset_id);
"""

_SNAPSHOT_COLUMNS: Final = (
    "snapshot_id",
    "dataset_id",
    "parent_snapshot_id",
    "created_at",
    "created_by",
    "dataset_name",
    "dataset_path",
    "num_rows",
    "num_columns",
)


def index_path(store_root: Path) -> Path:
    """Return the path of the store's index. Need not exist.

    Args:
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        The index's path, inside the store's gitignored cache directory.
    """
    return store_root / CACHE_DIRNAME / INDEX_FILENAME


def index_exists(store_root: Path) -> bool:
    """Return whether the store's index has been built yet.

    Args:
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        ``True`` if an index file is present. It may still turn out to be
        unusable, in which case [sync][dstrack.cache.sync] rebuilds it.
    """
    return index_path(store_root).is_file()


def sync(store_root: Path) -> str | None:
    """Bring the index in line with the store's logs.

    Reimports each dataset whose ``log.jsonl`` changed since it was last
    indexed, and forgets datasets that are no longer on disk. Builds the index
    if it is absent, and rebuilds it if it cannot be used as-is.

    Args:
        store_root: Path to the ``.dstrack/`` directory, e.g. from
            [resolve_store_root][dstrack.paths.resolve_store_root].

    Returns:
        ``None`` if the index was built from scratch or updated in place, or a
        human-readable reason if an existing index had to be discarded and
        rebuilt, for the caller to report. Discarding is recovery, not an
        error: the index holds nothing that is not derived from the logs.

    Raises:
        StoreCorruptionError: If a dataset's log holds a malformed entry.
        OSError: If the store cannot be read, or the index cannot be written.
    """
    reason: str | None = None
    if index_exists(store_root):
        try:
            with _connect(store_root) as conn:
                _check_schema_version(conn)
                _sync(conn, store_root)
            return None
        except (sqlite3.DatabaseError, IndexUnusable) as e:
            reason = str(e)
            _log.warning(f"Rebuilding the snapshot index: {e}")

    _create_empty(store_root)
    with _connect(store_root) as conn:
        _sync(conn, store_root)
    return reason


def query_history(dataset_id: str, *, store_root: Path) -> list[LogEntry]:
    """Return a dataset's committed history, newest first.

    Walks back from the ``HEAD`` recorded by the last
    [sync][dstrack.cache.sync], rather than re-reading ``HEAD`` from disk.
    That is what keeps the answer self-consistent: a sync reads each dataset's
    ``HEAD`` immediately before importing its log, so every snapshot that head
    names is guaranteed to be indexed. Reading ``HEAD`` again afterwards could
    pick up a snapshot a concurrent `dstrack track` committed since, whose log
    line this index has not seen, which is indistinguishable from a log that
    has lost an entry.

    Call [sync][dstrack.cache.sync] first, or the answer is as old as the last
    one.

    Args:
        dataset_id: Dataset whose history to read.
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        The dataset's snapshots, ordered newest first and ending at the one
        with no parent. Empty if the dataset is not indexed, or has no
        committed snapshot yet.

    Raises:
        StoreCorruptionError: If the indexed lineage is missing an entry or
            loops back on itself.
    """
    with _connect(store_root) as conn:
        head_row = conn.execute(
            "SELECT head_snapshot_id FROM datasets WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchone()
        if head_row is None or head_row["head_snapshot_id"] is None:
            return []
        head: str = head_row["head_snapshot_id"]
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE dataset_id = ?", (dataset_id,)
        ).fetchall()
    index = {row["snapshot_id"]: _entry_from_row(row) for row in rows}
    return store.walk_lineage(index, head)


def find_dataset_by_path(dataset_path: str, *, store_root: Path) -> str | None:
    """Return the id of the dataset whose latest snapshot has this path.

    Matches the same way [write_snapshot][dstrack.store.write_snapshot] does:
    against each dataset's ``HEAD`` snapshot only, never its older ones. A
    dataset whose file has since been renamed therefore does not match the new
    path, and must be named by its id instead.

    Args:
        dataset_path: Relative POSIX path to match, computed against the same
            path root the snapshot was recorded with.
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        The matching ``dataset_id``, or ``None`` if no dataset's latest
        snapshot recorded this path.
    """
    with _connect(store_root) as conn:
        row = conn.execute(
            "SELECT d.dataset_id FROM datasets d "
            "JOIN snapshots s ON s.snapshot_id = d.head_snapshot_id "
            "WHERE s.dataset_path = ? ORDER BY d.dataset_id LIMIT 1",
            (dataset_path,),
        ).fetchone()
    if row is None:
        return None
    dataset_id: str = row["dataset_id"]
    return dataset_id


def list_datasets(*, store_root: Path) -> list[DatasetSummary]:
    """Return every indexed dataset, described by its latest snapshot.

    Args:
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        One summary per dataset, sorted by id. A dataset with no committed
        snapshot yet is included, with a ``head`` of ``None``.
    """
    with _connect(store_root) as conn:
        rows = conn.execute(
            "SELECT d.dataset_id AS id, s.* FROM datasets d "
            "LEFT JOIN snapshots s ON s.snapshot_id = d.head_snapshot_id "
            "ORDER BY d.dataset_id"
        ).fetchall()
    return [
        DatasetSummary(
            dataset_id=row["id"],
            head=_entry_from_row(row) if row["snapshot_id"] is not None else None,
        )
        for row in rows
    ]


@contextlib.contextmanager
def _connect(store_root: Path) -> Iterator[sqlite3.Connection]:
    """Open the index, creating its parent directory if needed.

    Args:
        store_root: Path to the ``.dstrack/`` directory.

    Yields:
        A connection with row access by name and foreign keys enforced, so a
        dataset's rows are cleared with it. Uncommitted work is rolled back if
        the block raises.
    """
    path = index_path(store_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    finally:
        conn.close()


def _create_empty(store_root: Path) -> None:
    """Replace the index with an empty one at the current schema version.

    Args:
        store_root: Path to the ``.dstrack/`` directory.

    Raises:
        OSError: If the old index cannot be removed or the new one written.
    """
    index_path(store_root).unlink(missing_ok=True)
    with _connect(store_root) as conn:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (_SCHEMA_VERSION,),
        )
        conn.commit()


def _check_schema_version(conn: sqlite3.Connection) -> None:
    """Raise unless the index was built by this version of the schema.

    Args:
        conn: A connection to the index.

    Raises:
        _IndexUnusable: If the index records a different schema version, or no
            version at all.
    """
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.DatabaseError as e:
        raise IndexUnusable(f"its schema could not be read ({e})") from e
    if row is None:
        raise IndexUnusable("it records no schema version")
    if row["value"] != _SCHEMA_VERSION:
        raise IndexUnusable(
            f"it was built for schema version {row['value']}, "
            f"but this dstrack expects {_SCHEMA_VERSION}"
        )


def _sync(conn: sqlite3.Connection, store_root: Path) -> None:
    """Reimport every dataset whose log changed, and forget deleted ones.

    Runs as a single transaction, so an interrupted sync leaves the index at
    its previous state rather than partly reimported.

    Args:
        conn: A connection to the index.
        store_root: Path to the ``.dstrack/`` directory.

    Raises:
        StoreCorruptionError: If a dataset's log holds a malformed entry.
        OSError: If the store cannot be read.
    """
    fingerprints = {
        row["dataset_id"]: (row["log_size"], row["log_mtime_ns"])
        for row in conn.execute(
            "SELECT dataset_id, log_size, log_mtime_ns FROM datasets"
        )
    }

    on_disk = store.list_dataset_ids(store_root=store_root)
    for dataset_id in on_disk:
        # HEAD before the log, so a concurrent `track` cannot move HEAD onto a
        # snapshot whose line this sync has already read past.
        head = store.read_head(dataset_id, store_root=store_root)
        fingerprint = _fingerprint(dataset_id, store_root=store_root)

        conn.execute(
            "INSERT INTO datasets (dataset_id, log_size, log_mtime_ns, head_snapshot_id) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(dataset_id) DO UPDATE SET "
            "log_size = excluded.log_size, "
            "log_mtime_ns = excluded.log_mtime_ns, "
            "head_snapshot_id = excluded.head_snapshot_id",
            (dataset_id, fingerprint[0], fingerprint[1], head),
        )

        if fingerprints.get(dataset_id) == fingerprint:
            continue
        _reimport(conn, dataset_id, store_root=store_root)

    for dataset_id in fingerprints.keys() - set(on_disk):
        conn.execute("DELETE FROM datasets WHERE dataset_id = ?", (dataset_id,))

    conn.commit()


def _reimport(conn: sqlite3.Connection, dataset_id: str, *, store_root: Path) -> None:
    """Replace a dataset's indexed rows with what its log currently says.

    The whole log is reparsed rather than appended from a recorded offset.
    ``log.jsonl`` is append-only in normal operation, but the store's own
    corruption messages instruct users to delete an offending line by hand, so
    an offset cannot be trusted to still point at the same entry. The logs are
    small by design, which is what makes reparsing the cheaper bet.

    Args:
        conn: A connection to the index, inside a transaction.
        dataset_id: Dataset to reimport.
        store_root: Path to the ``.dstrack/`` directory.

    Raises:
        StoreCorruptionError: If the log holds a malformed entry.
        OSError: If the log cannot be read.
    """
    conn.execute("DELETE FROM snapshots WHERE dataset_id = ?", (dataset_id,))
    entries = store.read_log_entries(dataset_id, store_root=store_root)
    placeholders = ", ".join("?" * len(_SNAPSHOT_COLUMNS))
    # A duplicated line is ignored rather than fatal: the first occurrence
    # wins, matching how the lineage walk would have reached it.
    conn.executemany(
        f"INSERT OR IGNORE INTO snapshots ({', '.join(_SNAPSHOT_COLUMNS)}) "
        f"VALUES ({placeholders})",
        [
            (
                entry.snapshot_id,
                dataset_id,
                entry.parent_snapshot_id,
                entry.created_at,
                entry.created_by,
                entry.dataset_name,
                entry.dataset_path,
                entry.num_rows,
                entry.num_columns,
            )
            for entry in entries
        ],
    )


def _fingerprint(dataset_id: str, *, store_root: Path) -> tuple[int, int]:
    """Return a cheap signature of a dataset's log, to detect changes.

    Size and modification time, the same trade-off git's index makes: it
    misses an edit that preserves both, which a rebuild is the answer to, and
    costs one ``stat`` rather than a reparse when nothing changed.

    Args:
        dataset_id: Dataset whose log to fingerprint.
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        The log's size and modification time in nanoseconds, or zeroes if the
        dataset has no log yet.
    """
    path = store.dataset_log_path(dataset_id, store_root=store_root)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (0, 0)
    return (stat.st_size, stat.st_mtime_ns)


def _entry_from_row(row: sqlite3.Row) -> LogEntry:
    """Build a [LogEntry][dstrack.store.LogEntry] from an indexed row.

    Args:
        row: A ``snapshots`` row, or a join that selects its columns.

    Returns:
        The row as a [LogEntry][dstrack.store.LogEntry].
    """
    return LogEntry(
        snapshot_id=row["snapshot_id"],
        parent_snapshot_id=row["parent_snapshot_id"],
        created_at=row["created_at"],
        created_by=row["created_by"],
        dataset_name=row["dataset_name"],
        dataset_path=row["dataset_path"],
        num_rows=row["num_rows"],
        num_columns=row["num_columns"],
    )
