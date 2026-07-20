"""Persistence of snapshots into the local store, and reads back out of it.

A snapshot is written under ``datasets/<dataset_id>/`` as its full JSON
payload, a one-line append to ``log.jsonl``, and an updated ``HEAD``. The
three are written in that order so a crash never leaves ``HEAD``
pointing at a snapshot that was not fully written. Re-tracking a source whose
recorded path matches an existing dataset's latest snapshot continues that
dataset's lineage rather than creating a new one.

The three writes are not a single atomic transaction, so two safeguards keep
readers consistent. A store-wide advisory lock serializes
the whole resolve-parent-then-write sequence, so concurrent writers cannot read
the same ``HEAD`` as their parent nor mint two datasets for one path. And
``HEAD`` -- written last -- is the single source of truth for what is committed:
recovery and path matching read the log entry that ``HEAD`` names rather than
trusting the final line, so a ``log.jsonl`` left one entry ahead by a crash
between the append and the ``HEAD`` write is ignored.

Reading history back is therefore *reachability-based*, not positional.
[read_log_entries][dstrack.store.read_log_entries] returns every line a dataset's log holds, including
entries no ``HEAD`` ever named; [walk_lineage][dstrack.store.walk_lineage] then follows
``parent_snapshot_id`` links back from ``HEAD`` to select the ones that are
actually committed. Neither "read every line" nor "read every line up to
``HEAD``" is correct: a crash between the append and the ``HEAD`` write leaves
an uncommitted entry behind, and the *next* successful write appends after it,
stranding it in the middle of the file rather than at the end. Only
reachability from ``HEAD`` tells the two apart.
"""

import contextlib
import json
import os
import sys
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from dstrack.errors import DatasetNotFoundError, StoreCorruptionError

# Lightweight identity fields copied from a snapshot into its `log.jsonl` line.
# A subset of ADR-0001's fields: only those the current builders produce.
_LOG_FIELDS: Final[tuple[str, ...]] = (
    "snapshot_id",
    "parent_snapshot_id",
    "created_at",
    "created_by",
    "dataset_name",
    "dataset_path",
    "num_rows",
    "num_columns",
)

_DATASETS_DIRNAME: Final = "datasets"
_LOG_FILENAME: Final = "log.jsonl"
_HEAD_FILENAME: Final = "HEAD"
_SNAPSHOTS_DIRNAME: Final = "snapshots"


@dataclass(frozen=True)
class LogEntry:
    """One snapshot as recorded in a dataset's ``log.jsonl``.

    The lightweight per-snapshot record, mirroring
    log fields rather than the full
    ``snapshots/<snapshot_id>.json`` payload.

    Every field except ``snapshot_id`` is optional. Log lines are written with
    ``payload.get(key)`` (see [write_snapshot][dstrack.store.write_snapshot]),
    so a snapshot built without a field records it as JSON null, and a line
    written by a different version of dstrack may omit it entirely.

    Attributes:
        snapshot_id: Identifier of the snapshot. Always present: an entry is
            only ever reached via ``HEAD`` or a ``parent_snapshot_id`` link,
            both of which name it.
        parent_snapshot_id: Snapshot this one succeeds, or ``None`` for the
            dataset's first snapshot.
        created_at: ISO-8601 UTC timestamp, kept as the raw recorded string.
            Deliberately not parsed here: an unparsable value is a display
            concern, not a reason to fail a read.
        created_by: Recorded author, or ``None`` if not recorded.
        dataset_name: Human-readable name at snapshot time, or ``None``.
        dataset_path: POSIX path relative to the path root in force at
            snapshot time, or ``None``.
        num_rows: Row count, or ``None`` if not recorded.
        num_columns: Column count, or ``None`` if not recorded.
    """

    snapshot_id: str
    parent_snapshot_id: str | None
    created_at: str | None
    created_by: str | None
    dataset_name: str | None
    dataset_path: str | None
    num_rows: int | None
    num_columns: int | None


@dataclass(frozen=True)
class DatasetSummary:
    """A dataset in the store, described by its latest committed snapshot.

    Attributes:
        dataset_id: The dataset's directory name under ``datasets/``.
        head: The log entry the dataset's ``HEAD`` names, or ``None`` if the
            dataset has no committed snapshot yet.
    """

    dataset_id: str
    head: LogEntry | None


@dataclass(frozen=True)
class SnapshotWriteResult:
    """Outcome of writing one snapshot into the store.

    Attributes:
        dataset_id: Dataset whose lineage the snapshot joined, whether it was
            passed in, matched by path, or minted by this write.
        snapshot_id: Identifier of the snapshot that was written, copied from
            the snapshot payload.
        snapshot_path: Path of the written ``snapshots/<snapshot_id>.json``.
        parent_snapshot_id: Snapshot this one succeeds, i.e. the dataset's
            ``HEAD`` before this write, or ``None`` for a dataset's first
            snapshot.
        is_new_dataset: Whether this write minted a new ``dataset_id``, as
            opposed to continuing an existing dataset's lineage.
    """

    dataset_id: str
    snapshot_id: str
    snapshot_path: Path
    parent_snapshot_id: str | None
    is_new_dataset: bool


def write_snapshot(
    snapshot: dict[str, Any],
    *,
    store_root: Path,
    dataset_id: str | None = None,
) -> SnapshotWriteResult:
    """Write ``snapshot`` into the store and update the dataset's history.

    Resolves which dataset the snapshot belongs to, then writes the full
    payload, appends the lightweight ``log.jsonl`` line, and moves ``HEAD``
    onto the new snapshot.

    Args:
        snapshot: A snapshot dict as produced by
            [SnapshotBuilder][dstrack.snapshot._builder.SnapshotBuilder]. Must contain
            ``snapshot_id`` and ``dataset_path``; ``parent_snapshot_id`` is
            filled in here and overwritten if already present.
        store_root: Path to the ``.dstrack/`` directory, e.g. from
            [resolve_store_root][dstrack.paths.resolve_store_root].
        dataset_id: Continue this dataset's lineage explicitly. The dataset must
            already exist. When ``None``, the dataset is matched by
            ``dataset_path`` against each existing dataset's latest snapshot; a
            new ``dataset_id`` is minted if none matches.

    Returns:
        A [SnapshotWriteResult][dstrack.store.SnapshotWriteResult] describing
        where the snapshot landed and which lineage it joined.

    Raises:
        KeyError: If ``snapshot`` lacks ``snapshot_id`` or ``dataset_path``.
        ValueError: If ``snapshot_id`` or an explicit ``dataset_id`` resolves to
            a path outside the store.
        DatasetNotFoundError: If ``dataset_id`` is given but names no dataset in
            the store.
        StoreCorruptionError: If an existing dataset's ``log.jsonl`` ends in a
            line that is not valid JSON.
        OSError: If the store cannot be written to.
    """
    datasets_dir = _datasets_dir(store_root)
    dataset_path = snapshot["dataset_path"]
    snapshot_id = snapshot["snapshot_id"]

    # Lock the store to avoid cross-process contamination
    with _store_lock(store_root):
        if dataset_id is not None:
            # An explicit id continues a lineage; it must name a dataset
            _ensure_direct_child(
                base=datasets_dir,
                candidate=datasets_dir / dataset_id,
                kind="dataset_id",
                value=dataset_id,
            )
            _check_dataset_exists(datasets_dir, dataset_id)
            is_new_dataset = False
        else:
            dataset_id = _match_dataset_by_path(datasets_dir, dataset_path)
            is_new_dataset = dataset_id is None
            if dataset_id is None:
                dataset_id = str(uuid.uuid4())

        dataset_dir = datasets_dir / dataset_id
        snapshots_dir = dataset_dir / _SNAPSHOTS_DIRNAME
        snapshot_path = snapshots_dir / f"{snapshot_id}.json"
        # snapshot_id arrives in the payload from outside, so confirm the file
        # lands inside the dataset's snapshots/ before creating any directory.
        _ensure_direct_child(
            base=snapshots_dir,
            candidate=snapshot_path,
            kind="snapshot_id",
            value=snapshot_id,
        )
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        # HEAD is the dataset's latest snapshot, so it is this snapshot's parent
        parent_snapshot_id = _read_head(dataset_dir)
        payload = {**snapshot, "parent_snapshot_id": parent_snapshot_id}

        _atomic_write(snapshot_path, json.dumps(payload, indent=2) + "\n")

        log_line = {key: payload.get(key) for key in _LOG_FIELDS}
        with (dataset_dir / _LOG_FILENAME).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(log_line) + "\n")

        _atomic_write(dataset_dir / _HEAD_FILENAME, snapshot_id + "\n")

    return SnapshotWriteResult(
        dataset_id=dataset_id,
        snapshot_id=snapshot_id,
        snapshot_path=snapshot_path,
        parent_snapshot_id=parent_snapshot_id,
        is_new_dataset=is_new_dataset,
    )


def list_dataset_ids(*, store_root: Path) -> list[str]:
    """Return the id of every dataset in the store, sorted.

    Args:
        store_root: Path to the ``.dstrack/`` directory, e.g. from
            [resolve_store_root][dstrack.paths.resolve_store_root].

    Returns:
        Each dataset's id, i.e. its directory name under ``datasets/``. Empty
        if the store holds no datasets yet.
    """
    return [d.name for d in _iter_dataset_dirs(_datasets_dir(store_root))]


def dataset_exists(dataset_id: str, *, store_root: Path) -> bool:
    """Return whether the store holds a dataset with this id.

    Args:
        dataset_id: The dataset id, which may come from the user.
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        ``True`` if the dataset has a directory in the store, whether or not
        it has been snapshotted yet.

    Raises:
        ValueError: If ``dataset_id`` resolves to a path outside the store.
    """
    return _resolve_dataset_dir(dataset_id, store_root).is_dir()


def read_head(dataset_id: str, *, store_root: Path) -> str | None:
    """Return the id of the snapshot a dataset's ``HEAD`` names.

    ``HEAD`` is the single source of truth for what a dataset has committed,
    so callers that derive history from it should read it *before* reading
    ``log.jsonl``: a snapshot's log line is appended before the ``HEAD`` write
    that names it, so any ``HEAD`` observed at a given moment is guaranteed to
    have its whole ancestry already on disk. Reading them the other way round
    can observe a ``HEAD`` naming a snapshot whose line was appended after the
    log was read, which looks indistinguishable from corruption.

    Args:
        dataset_id: Dataset whose ``HEAD`` to read.
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        The latest committed snapshot's id, or ``None`` if the dataset does
        not exist or has never been snapshotted.

    Raises:
        ValueError: If ``dataset_id`` resolves to a path outside the store.
    """
    return _read_head(_resolve_dataset_dir(dataset_id, store_root))


def dataset_log_path(dataset_id: str, *, store_root: Path) -> Path:
    """Return the path of a dataset's ``log.jsonl``. Need not exist.

    Args:
        dataset_id: Dataset whose log to locate.
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        The path of the dataset's append-only log.

    Raises:
        ValueError: If ``dataset_id`` resolves to a path outside the store.
    """
    return _resolve_dataset_dir(dataset_id, store_root) / _LOG_FILENAME


def read_log_entries(dataset_id: str, *, store_root: Path) -> list[LogEntry]:
    """Return every entry in a dataset's ``log.jsonl``, in file order.

    Includes entries that no ``HEAD`` ever named, so the result is what the
    log *says*, not what the dataset has *committed*. Pass the result through
    [walk_lineage][dstrack.store.walk_lineage] to select the committed
    lineage.

    A trailing line that is not valid JSON and is not newline-terminated is
    treated as an interrupted append and dropped, not reported as corruption:
    the log is appended to before ``HEAD`` moves, so a torn final line is a
    snapshot that was never committed and is expected after a crash. A
    malformed line anywhere else was written completely and is corruption.

    Args:
        dataset_id: Dataset whose log to read.
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        One entry per log line, oldest first. Empty if the dataset does not
        exist or has no log yet.

    Raises:
        ValueError: If ``dataset_id`` resolves to a path outside the store.
        StoreCorruptionError: If a complete log line is not a valid JSON
            object, or records a ``snapshot_id`` or ``parent_snapshot_id``
            that is not a string.
        OSError: If the log cannot be read.
    """
    path = dataset_log_path(dataset_id, store_root=store_root)
    if not path.is_file():
        return []

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    entries: list[LogEntry] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            is_torn_append = index == len(lines) - 1 and not line.endswith("\n")
            if is_torn_append:
                break
            raise StoreCorruptionError(
                f"An entry of `{path}` is not valid JSON. The file may have "
                "been truncated by an interrupted write; restore it from git "
                f"or delete the offending line. Error found in line loc {index}."
            ) from e
        if not isinstance(data, dict):
            raise StoreCorruptionError(
                f"An entry of `{path}` is not a JSON object. Restore the file "
                "from git or delete the offending line."
            )
        entries.append(_log_entry_from_line(data, path=path))
    return entries


def walk_lineage(index: Mapping[str, LogEntry], head: str) -> list[LogEntry]:
    """Select the snapshots reachable from ``head``, newest first.

    Follows ``parent_snapshot_id`` links back from ``head``, which is what
    makes a history *committed* rather than merely *recorded*. Entries the
    walk does not reach are ignored: a crash between a log append and the
    ``HEAD`` write (see [write_snapshot][dstrack.store.write_snapshot]) leaves
    an entry the store never committed, and a later successful write appends
    after it, stranding it mid-file rather than at the end. Reachability is
    the only thing that tells such an entry apart from a real snapshot.

    Args:
        index: Every entry the dataset's log holds, keyed by ``snapshot_id``,
            e.g. built from [read_log_entries][dstrack.store.read_log_entries].
        head: Id of the snapshot to walk back from, i.e. the dataset's
            ``HEAD``.

    Returns:
        The lineage ``head`` names, ordered newest first and ending at the
        snapshot with no parent.

    Raises:
        StoreCorruptionError: If the lineage names a snapshot ``index`` has no
            entry for, or if the ``parent_snapshot_id`` links form a cycle.
    """
    lineage: list[LogEntry] = []
    seen: set[str] = set()
    current: str | None = head
    child: str | None = None

    while current is not None:
        if current in seen:
            raise StoreCorruptionError(
                f"The history of snapshot {head!r} loops back on itself at "
                f"{current!r}. A snapshot cannot be its own ancestor; the log "
                "has been edited into a cycle. Restore it from git."
            )
        seen.add(current)

        entry = index.get(current)
        if entry is None:
            missing = (
                f"The log has no entry for snapshot {current!r}"
                if child is None
                else f"The log has no entry for snapshot {current!r}, named as "
                f"the parent of {child!r}"
            )
            raise StoreCorruptionError(
                f"{missing}. Part of the dataset's lineage is missing; restore "
                "the log from git."
            )

        lineage.append(entry)
        child = current
        current = entry.parent_snapshot_id

    return lineage


def _log_entry_from_line(line: dict[str, Any], *, path: Path) -> LogEntry:
    """Build a [LogEntry][dstrack.store.LogEntry] from a parsed log line.

    Reads each field by name and ignores any it does not know, rather than
    unpacking the line. A store written by a newer dstrack  would make ``LogEntry(**line)`` raise, and
    ``.dstrack/`` is committed to git, so such a store legitimately reaches an
    older client by way of a clone or a pull.

    Fields are validated by the job they do. ``snapshot_id`` and
    ``parent_snapshot_id`` are structural: they drive
    [walk_lineage][dstrack.store.walk_lineage], so a wrong type there is
    corruption. The rest are cosmetic, and a wrong type is coerced to ``None``
    for the caller to render as missing: the store's own corruption messages
    tell users to hand-edit the log, so hand-edited damage is expected, and a
    display command must not die on a field it only prints.

    Args:
        line: A parsed ``log.jsonl`` line.
        path: The log the line came from, used in error messages.

    Returns:
        The line as a [LogEntry][dstrack.store.LogEntry].

    Raises:
        StoreCorruptionError: If ``snapshot_id`` is missing, empty, or not a
            string, or if ``parent_snapshot_id`` is present but not a string.
    """
    snapshot_id = line.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise StoreCorruptionError(
            f"An entry of `{path}` has no usable `snapshot_id`. Every entry "
            "must identify its snapshot; restore the file from git or delete "
            "the offending line."
        )

    parent_snapshot_id = line.get("parent_snapshot_id")
    if parent_snapshot_id is not None and not isinstance(parent_snapshot_id, str):
        raise StoreCorruptionError(
            f"Entry {snapshot_id!r} of `{path}` records a "
            "`parent_snapshot_id` that is not a snapshot id. Restore the file "
            "from git or delete the offending line."
        )

    return LogEntry(
        snapshot_id=snapshot_id,
        parent_snapshot_id=parent_snapshot_id,
        created_at=_optional_str(line.get("created_at")),
        created_by=_optional_str(line.get("created_by")),
        dataset_name=_optional_str(line.get("dataset_name")),
        dataset_path=_optional_str(line.get("dataset_path")),
        num_rows=_optional_int(line.get("num_rows")),
        num_columns=_optional_int(line.get("num_columns")),
    )


def _optional_str(value: Any) -> str | None:
    """Return `value` if it is a string, else ``None``."""
    return value if isinstance(value, str) else None


def _optional_int(value: Any) -> int | None:
    """Return `value` if it is an integer, else ``None``.

    ``bool`` is a subclass of ``int``, so it is rejected explicitly: a count
    recorded as ``true`` is damage, not the number one.
    """
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _datasets_dir(store_root: Path) -> Path:
    """Return the store's ``datasets/`` directory. Need not exist.

    Args:
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        The directory every dataset lives directly inside.
    """
    return store_root / _DATASETS_DIRNAME


def _iter_dataset_dirs(datasets_dir: Path) -> Iterator[Path]:
    """Yield each dataset's directory, in a stable order.

    Args:
        datasets_dir: The store's ``datasets/`` directory. Need not exist.

    Yields:
        Every directory directly inside ``datasets_dir``, sorted by name.
        Non-directory entries are skipped, so a stray file in the store does
        not read as a dataset.
    """
    if not datasets_dir.is_dir():
        return
    for dataset_dir in sorted(datasets_dir.iterdir()):
        if dataset_dir.is_dir():
            yield dataset_dir


def _resolve_dataset_dir(dataset_id: str, store_root: Path) -> Path:
    """Return a dataset's directory, rejecting an id that escapes the store.

    Args:
        dataset_id: The dataset id, which may come from the user.
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        The ``datasets/<dataset_id>/`` directory. Need not exist.

    Raises:
        ValueError: If ``dataset_id`` does not name a direct child of
            ``datasets/``.
    """
    datasets_dir = _datasets_dir(store_root)
    dataset_dir = datasets_dir / dataset_id
    _ensure_direct_child(
        base=datasets_dir,
        candidate=dataset_dir,
        kind="dataset_id",
        value=dataset_id,
    )
    return dataset_dir


def _ensure_direct_child(base: Path, candidate: Path, kind: str, value: str) -> None:
    """Raise unless ``candidate`` resolves to a direct child of ``base``.

    ``dataset_id`` and ``snapshot_id`` are joined onto the store to form a single
    directory or file name directly under ``base``. A value holding a ``..``
    segment, a path separator, or an absolute path can make ``candidate`` land
    outside ``base`` (and clobber files elsewhere) or nest under a subdirectory
    that does not exist. Requiring the resolved parent to equal ``base`` rejects
    both, regardless of how the escape is spelled, before any file is written.

    Args:
        base: Directory the candidate must sit directly under, e.g. the store's
            ``datasets/`` directory.
        candidate: The joined path derived from ``value``.
        kind: Name of the identifier, used in the error message.
        value: The identifier value, used in the error message.

    Raises:
        ValueError: If ``candidate`` does not resolve to a direct child of
            ``base``.
    """
    if candidate.resolve().parent != base.resolve():
        raise ValueError(
            f"Invalid {kind} {value!r}: it must name a single entry directly "
            "inside the store, not a nested or outside path."
        )


def _check_dataset_exists(datasets_dir: Path, dataset_id: str) -> None:
    """Raise unless ``dataset_id`` names a dataset already in the store.

    Args:
        datasets_dir: The store's ``datasets/`` directory. Need not exist.
        dataset_id: The dataset id the caller asked to continue.

    Raises:
        DatasetNotFoundError: If the store holds no such dataset.
    """
    if (datasets_dir / dataset_id).is_dir():
        return
    known: list[str] = []
    if datasets_dir.is_dir():
        known = sorted(d.name for d in datasets_dir.iterdir() if d.is_dir())
    listing = "\n".join(f"  {d}" for d in known) or "  (none)"
    raise DatasetNotFoundError(
        f"No dataset {dataset_id!r} in the store. Omit --dataset-id to match "
        f"the dataset by path, or start a new lineage.\nKnown datasets:\n{listing}"
    )


def _match_dataset_by_path(datasets_dir: Path, dataset_path: str) -> str | None:
    """Return the id of the dataset whose latest snapshot has this path.

    Compares ``dataset_path`` against the last ``log.jsonl`` entry of every
    existing dataset, the cheap lightweight record rather than the full
    snapshot JSON.

    Args:
        datasets_dir: The store's ``datasets/`` directory. Need not exist.
        dataset_path: Relative POSIX path recorded for the snapshot being
            written, as computed against the path root.

    Returns:
        The ``dataset_id`` of the first dataset whose latest snapshot recorded
        the same path, or ``None`` if the store holds no dataset that matches.

    Raises:
        StoreCorruptionError: If a dataset's ``log.jsonl`` ends in a line that
            is not valid JSON.
    """
    for dataset_dir in _iter_dataset_dirs(datasets_dir):
        entry = _read_committed_log_entry(dataset_dir)
        if entry is not None and entry.get("dataset_path") == dataset_path:
            return dataset_dir.name
    return None


def _read_committed_log_entry(dataset_dir: Path) -> dict[str, Any] | None:
    """Return the ``log.jsonl`` entry for the snapshot ``HEAD`` names.

    ``HEAD`` is written last, so it is the single source of truth for what is
    committed. The final log line is not: a crash between the ``log.jsonl``
    append and the ``HEAD`` write can leave the log one entry ahead of ``HEAD``.
    So the committed entry is the one whose ``snapshot_id`` equals ``HEAD``,
    which is guaranteed to be present because its append precedes the ``HEAD``
    write that names it.

    Args:
        dataset_dir: A ``datasets/<dataset_id>/`` directory.

    Returns:
        The parsed log entry the dataset's ``HEAD`` points at, or ``None`` if
        the dataset has no ``HEAD`` or no ``log.jsonl`` yet.

    Raises:
        StoreCorruptionError: If a log line is not valid JSON, or no entry
            matches ``HEAD`` (the log lost the committed snapshot's line).
    """
    head = _read_head(dataset_dir)
    if head is None:
        return None
    log_path = dataset_dir / _LOG_FILENAME
    if not log_path.is_file():
        return None
    with log_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                entry: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError as e:
                raise StoreCorruptionError(
                    f"An entry of `{log_path}` is not valid JSON. The file may "
                    "have been truncated by an interrupted write; restore it "
                    "from git or delete the offending line."
                ) from e
            if entry.get("snapshot_id") == head:
                return entry
    raise StoreCorruptionError(
        f"`{log_path}` has no entry for HEAD {head!r}. The log is missing the "
        "committed snapshot's line; restore it from git."
    )


def _read_head(dataset_dir: Path) -> str | None:
    """Return the snapshot_id recorded in a dataset's ``HEAD``.

    Args:
        dataset_dir: A ``datasets/<dataset_id>/`` directory.

    Returns:
        The latest snapshot's id, or ``None`` if the dataset has no ``HEAD``
        yet, i.e. it has never been snapshotted.
    """
    head_path = dataset_dir / _HEAD_FILENAME
    if not head_path.is_file():
        return None
    head = head_path.read_text(encoding="utf-8").strip()
    return head or None


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically via a temp file and ``os.replace``.

    The temp file is created in the destination directory so the final rename
    stays on the same filesystem and is therefore atomic. Readers only ever see
    the old content or the new one, never a partial write.

    Args:
        path: File to create or replace. Its parent directory must exist.
        text: Full content to write, encoded as UTF-8.

    Raises:
        OSError: If the temp file cannot be written or renamed into place. The
            temp file is removed and ``path`` is left untouched.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


@contextlib.contextmanager
def _store_lock(store_root: Path) -> Iterator[None]:
    """Hold an exclusive, cross-process lock on the store for the block's body.

    Serializes writers across processes so the resolve-parent-then-write
    sequence in [write_snapshot][dstrack.store.write_snapshot] commits as a
    unit: no two writers observe the same ``HEAD`` as a parent, and no two
    path-matched writers mint separate datasets for one path. The lock is a
    single ``.lock`` file at the store root, held for every dataset rather than
    per-dataset, which also covers the cross-dataset scan that path matching
    performs. It is an OS advisory lock, so it is released automatically if the
    holding process dies, leaving no stale lock to clear by hand.

    Args:
        store_root: Path to the ``.dstrack/`` directory. Created if absent so
            the lock file has somewhere to live.

    Yields:
        Nothing; the caller runs its critical section inside the ``with``.
    """
    store_root.mkdir(parents=True, exist_ok=True)
    # Open without truncating: the file is a lock handle, its contents unused.
    fh = (store_root / ".lock").open("a", encoding="utf-8")
    try:
        _acquire(fh)
        try:
            yield
        finally:
            _release(fh)
    finally:
        fh.close()


if sys.platform == "win32":  # pragma: no cover - platform specific
    import msvcrt

    def _acquire(fh: Any) -> None:
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)

    def _release(fh: Any) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _acquire(fh: Any) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    def _release(fh: Any) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
