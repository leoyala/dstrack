"""Persistence of snapshots into the local store.

A snapshot is written under ``datasets/<dataset_id>/`` as its full JSON
payload, a one-line append to ``log.jsonl``, and an updated ``HEAD``. The
three are written in that order so a crash never leaves ``HEAD``
pointing at a snapshot that was not fully written. Re-tracking a source whose
recorded path matches an existing dataset's latest snapshot continues that
dataset's lineage rather than creating a new one.

The three writes are not a single atomic transaction, so two safeguards keep
readers consistent. A store-wide advisory lock (:func:`_store_lock`) serializes
the whole resolve-parent-then-write sequence, so concurrent writers cannot read
the same ``HEAD`` as their parent nor mint two datasets for one path. And
``HEAD`` -- written last -- is the single source of truth for what is committed:
recovery and path matching read the log entry that ``HEAD`` names rather than
trusting the final line, so a ``log.jsonl`` left one entry ahead by a crash
between the append and the ``HEAD`` write is ignored.
"""

import contextlib
import json
import os
import sys
import tempfile
import uuid
from collections.abc import Iterator
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
    datasets_dir = store_root / "datasets"
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
        snapshots_dir = dataset_dir / "snapshots"
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
        with (dataset_dir / "log.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(log_line) + "\n")

        _atomic_write(dataset_dir / "HEAD", snapshot_id + "\n")

    return SnapshotWriteResult(
        dataset_id=dataset_id,
        snapshot_id=snapshot_id,
        snapshot_path=snapshot_path,
        parent_snapshot_id=parent_snapshot_id,
        is_new_dataset=is_new_dataset,
    )


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
    if not datasets_dir.is_dir():
        return None
    for dataset_dir in sorted(datasets_dir.iterdir()):
        if not dataset_dir.is_dir():
            continue
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
    log_path = dataset_dir / "log.jsonl"
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
    head_path = dataset_dir / "HEAD"
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
