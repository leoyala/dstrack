"""Turning what a user typed into something the store can be asked about.

Commands take a dataset or a snapshot the way a person refers to it -- a path,
an id, the first few characters of one -- and the store only answers to exact
ids. This module holds that translation, and the consent flow that must run
before the index it relies on is built.

It lives here rather than in `dstrack.store` or `dstrack.cache` because
resolution is a *command's* concern: it prompts, prints, and exits, none of
which a data-layer module may do. And it lives in one place rather than in each
command because the precedence between an id and a path is a promise made to
the user, and two copies of it would drift until `dstrack log` and
`dstrack diff` answered the same argument differently.
"""

import os
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import typer

from dstrack import cache, console, store
from dstrack.errors import (
    AmbiguousSnapshotError,
    DatasetNotFoundError,
    SnapshotNotFoundError,
    StoreCorruptionError,
)

# Shortest prefix accepted as a snapshot id. Below this almost any id matches,
# and the argument is far more likely to be a path.
_MIN_PREFIX_LEN: Final = 4
# Longest, i.e. a full canonical UUID.
_MAX_PREFIX_LEN: Final = 36
_HEX_CHARS: Final = frozenset("0123456789abcdefABCDEF-")
_SHORT_ID_LEN: Final = 8
# Candidates listed back when a prefix is ambiguous. Enough to recognize the
# one that was meant, not a dump of the store.
_MAX_CANDIDATES: Final = 10
# A comparison has exactly two sides.
_PAIR: Final = 2


@dataclass(frozen=True)
class ResolvedSnapshot:
    """One command-line argument, resolved to a snapshot and its payload.

    Attributes:
        dataset_id: Dataset the snapshot belongs to.
        snapshot_id: The snapshot itself.
        payload: Its full stored payload.
        spec: What the user typed, kept for error messages that follow.
    """

    dataset_id: str
    snapshot_id: str
    payload: dict[str, Any]
    spec: str


def ensure_index(
    store_root: Path, *, yes: bool, rebuild: bool, quiet: bool = False
) -> None:
    """Make sure the snapshot index may be built, asking first if need be.

    Building parses every dataset's log, so it is offered rather than done
    silently. Consent is only about the first build: an index that exists but
    cannot be used is rebuilt without asking, since it holds nothing that the
    logs do not.

    Args:
        store_root: Path to the ``.dstrack/`` directory.
        yes: Consent given up front, so nothing is asked.
        rebuild: Discard any existing index, which implies consent.
        quiet: Suppress the progress notes, for a command whose standard
            output is a document rather than a report to a person. Errors are
            still printed: a failure the caller has to act on is not noise.

    Raises:
        typer.Exit: Code 0 if the user declines to build it; code 1 if there
            is no index and no terminal to ask at.
    """
    path = cache.index_path(store_root)
    if rebuild:
        path.unlink(missing_ok=True)
        if not quiet:
            console.info("Rebuilding the snapshot index from the dataset logs...")
        return
    if cache.index_exists(store_root):
        return

    if not yes:
        if not sys.stdin.isatty():
            console.error(
                f"No snapshot index at `{path}`, and there is no terminal to "
                "ask whether to build one. Re-run with `--yes` to build it."
            )
            raise typer.Exit(code=1)
        console.info(f"No snapshot index at `{path}` yet.")
        if not typer.confirm("Build it now from the dataset logs?"):
            console.warning(
                "Not building the index; there is nothing to show without it."
            )
            raise typer.Exit(code=0)

    if not quiet:
        console.info("Building the snapshot index from the dataset logs...")


def resolve_target(target: str, *, store_root: Path, root: Path | None) -> str:
    """Resolve a user-supplied dataset id or path to a dataset id.

    A ``dataset_id`` is a random UUID4, so nothing about it can be
    derived from a path: a path is resolved by matching it against what each
    dataset last recorded. `target` is read as an id first and as a path
    second, but only when the id names a dataset that exists. Reading it as a
    path first would let any file in the current directory shadow a real id,
    and would send a mistyped path into the id lookup, which can only report
    that no dataset goes by that name.

    Args:
        target: A dataset id, or a path to a tracked dataset file.
        store_root: Path to the ``.dstrack/`` directory.
        root: Path root a `target` path is made relative to, or ``None`` to
            use the store root.

    Returns:
        The id of the dataset `target` names.

    Raises:
        DatasetNotFoundError: If `target` is an id no dataset goes by, or a
            path no dataset last recorded.
        ValueError: If `target` and the path root are on different drives, so
            no relative path exists between them.
    """
    canonical = canonical_uuid(target)
    if canonical is not None and store.dataset_exists(canonical, store_root=store_root):
        return canonical

    dataset_path = recorded_path(target, store_root=store_root, root=root)

    matched = cache.find_dataset_by_path(dataset_path, store_root=store_root)
    if matched is not None:
        return matched

    raise dataset_not_found(
        target,
        canonical=canonical,
        dataset_path=dataset_path,
        store_root=store_root,
        root=root,
    )


def recorded_path(target: str, *, store_root: Path, root: Path | None) -> str:
    """Return the relative POSIX path a dataset at `target` would be recorded as.

    Mirrors what `dstrack track` stores, so that a path typed on the command
    line and a path read out of a snapshot can be compared as strings.

    Args:
        target: A path to a dataset file, as the user typed it.
        store_root: Path to the ``.dstrack/`` directory.
        root: Path root the result is made relative to, or ``None`` to use the
            store root, i.e. the directory containing ``.dstrack/``.

    Returns:
        The path relative to the path root, in POSIX form.

    Raises:
        ValueError: If `target` and the path root are on different drives, so
            no relative path exists between them.
    """
    path_root = root if root is not None else store_root.parent
    return Path(
        os.path.relpath(Path(target).resolve(), Path(path_root).resolve())
    ).as_posix()


def dataset_not_found(
    target: str,
    *,
    canonical: str | None,
    dataset_path: str,
    store_root: Path,
    root: Path | None,
) -> Exception:
    """Build the error for a `target` that names no dataset.

    An id-shaped argument and a path-shaped one fail for different reasons and
    are fixed in different ways, so they are reported differently. Both end on
    the store's contents, which is what lets a reader recognize the dataset
    they meant.

    Args:
        target: The argument as the user typed it.
        canonical: `target` as a canonical UUID, or ``None`` if it is not one.
        dataset_path: What `target` was matched as, when read as a path.
        store_root: Path to the ``.dstrack/`` directory.
        root: Path root `dataset_path` was computed against, or ``None``.

    Returns:
        The exception to raise. Returned rather than raised so the caller's
        traceback starts at the failure, not inside this helper.
    """
    if canonical is not None:
        return DatasetNotFoundError(
            f"No dataset {target!r} in the store.\n{known_datasets(store_root)}"
        )
    path_root = root if root is not None else store_root.parent
    return DatasetNotFoundError(
        f"No tracked dataset at {target!r} (recorded as {dataset_path!r} "
        f"relative to {path_root}).\n"
        "If the file was renamed or moved, its recorded path no longer "
        "matches; pass the dataset id instead. Use `--root` to change the "
        "path root the recorded path is computed against.\n"
        f"{known_datasets(store_root)}"
    )


def canonical_uuid(value: str) -> str | None:
    """Return `value` as a canonical UUID string, or ``None`` if it is not one.

    Datasets are named by ``str(uuid.uuid4())``, but a user may paste an id
    uppercased, braced, or without its dashes, all of which name the same
    dataset. Canonicalizing accepts those spellings, and makes the result
    provably free of path separators before it is joined onto the store.

    Args:
        value: The string to interpret.

    Returns:
        The canonical form of `value` if it is a UUID in any spelling, else
        ``None``.
    """
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        return None


def known_datasets(store_root: Path) -> str:
    """List the store's datasets, for an error message to end on.

    A dataset id says nothing on its own, so each is shown with the name and
    path its latest snapshot recorded: that is what lets a reader recognize
    the dataset they meant and copy its id.

    Args:
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        An indented listing of every dataset, or a note that there are none.
    """
    datasets = cache.list_datasets(store_root=store_root)
    lines = [
        f"  {d.dataset_id}  {(d.head.dataset_name if d.head else None) or '-'}  "
        f"{(d.head.dataset_path if d.head else None) or '-'}"
        for d in datasets
    ]
    if not lines:
        return "Known datasets:\n  (none)\nRun `dstrack track <path>` to record one."
    listing = "\n".join(lines)
    return f"Known datasets:\n{listing}"


def resolve_snapshot_ref(
    spec: str, *, store_root: Path, root: Path | None
) -> ResolvedSnapshot:
    """Resolve one user-supplied reference to a snapshot.

    A reference is read in the order that keeps each reading from shadowing
    the one before it:

    1. a full id, which names either a snapshot outright or a dataset whose
       latest snapshot is meant;
    2. the path of a tracked file, matched as `dstrack log` matches it;
    3. the leading characters of a snapshot id, the way git takes a short
       commit hash.

    The prefix is tried *after* the path so that a file whose name happens to
    be eight hex characters keeps the meaning `dstrack log` already gives it,
    and *before* the failure so that a mistyped path is reported as a path
    rather than as an unknown id.

    Args:
        spec: The reference, as the user typed it.
        store_root: Path to the ``.dstrack/`` directory.
        root: Path root a `spec` path is made relative to, or ``None`` to use
            the store root.

    Returns:
        The snapshot it names, with its payload already read.

    Raises:
        AmbiguousSnapshotError: If a prefix matches more than one snapshot.
        DatasetNotFoundError: If `spec` names nothing in the store.
        SnapshotNotFoundError: If it names a dataset that has no snapshots.
        StoreCorruptionError: If the payload cannot be read back.
        ValueError: If `spec` and the path root are on different drives.
    """
    canonical = canonical_uuid(spec)
    if canonical is not None:
        located = _match_prefix(canonical, spec, store_root=store_root)
        if located is not None:
            return _load(located, spec, store_root=store_root)
        if store.dataset_exists(canonical, store_root=store_root):
            return _head_of(canonical, spec, store_root=store_root)

    dataset_path = recorded_path(spec, store_root=store_root, root=root)
    matched = cache.find_dataset_by_path(dataset_path, store_root=store_root)
    if matched is not None:
        return _head_of(matched, spec, store_root=store_root)

    if canonical is None and _looks_like_prefix(spec):
        located = _match_prefix(spec, spec, store_root=store_root)
        if located is not None:
            return _load(located, spec, store_root=store_root)

    raise dataset_not_found(
        spec,
        canonical=canonical,
        dataset_path=dataset_path,
        store_root=store_root,
        root=root,
    )


def resolve_pair(
    specs: Sequence[str],
    *,
    from_ref: str | None,
    to_ref: str | None,
    store_root: Path,
    root: Path | None,
) -> tuple[ResolvedSnapshot, ResolvedSnapshot]:
    """Resolve the two sides of a comparison from what the command was given.

    Two spellings are accepted and cannot be mixed: positional arguments, or
    the explicit `--from` / `--to` options. Naming one side leaves the other
    implied -- a single snapshot is compared against the one it succeeded,
    which is the question `dstrack diff` is usually asked.

    Args:
        specs: Positional references, in the order they were given.
        from_ref: The baseline, named explicitly.
        to_ref: The later snapshot, named explicitly.
        store_root: Path to the ``.dstrack/`` directory.
        root: Path root a path reference is made relative to.

    Returns:
        The baseline and the later snapshot, in that order.

    Raises:
        typer.BadParameter: If the two spellings are mixed, if more than two
            positional references are given, or if none is given at all.
        SnapshotNotFoundError: If an implied side does not exist, e.g. the
            parent of a dataset's first snapshot.
    """
    explicit = from_ref is not None or to_ref is not None
    if explicit and specs:
        raise typer.BadParameter(
            "Name the snapshots either positionally or with --from/--to, not both."
        )
    if len(specs) > _PAIR:
        raise typer.BadParameter(
            f"Expected at most two snapshots to compare, got {len(specs)}."
        )
    if not explicit and not specs:
        raise typer.BadParameter("Name at least one snapshot or dataset to compare.")

    def resolve(spec: str) -> ResolvedSnapshot:
        return resolve_snapshot_ref(spec, store_root=store_root, root=root)

    if from_ref is not None and to_ref is not None:
        return resolve(from_ref), resolve(to_ref)
    if to_ref is not None:
        new = resolve(to_ref)
        return _parent_of(new, store_root=store_root), new
    if from_ref is not None:
        # Only the baseline was named, so the question is "what has happened
        # since?", which the dataset's latest snapshot answers.
        old = resolve(from_ref)
        return old, _head_of(old.dataset_id, from_ref, store_root=store_root)

    if len(specs) == _PAIR:
        return resolve(specs[0]), resolve(specs[1])

    new = resolve(specs[0])
    return _parent_of(new, store_root=store_root), new


def _parent_of(snapshot: ResolvedSnapshot, *, store_root: Path) -> ResolvedSnapshot:
    """Return the snapshot a resolved one succeeded.

    The parent is read from the payload already in hand rather than looked up
    again: a snapshot records the one it followed, which makes it the
    authoritative answer and a free one.

    Args:
        snapshot: The snapshot whose predecessor is wanted.
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        The predecessor, with its payload read.

    Raises:
        SnapshotNotFoundError: If the snapshot has no parent, i.e. it is the
            first its dataset recorded.
    """
    parent = snapshot.payload.get("parent_snapshot_id")
    if not isinstance(parent, str) or not parent:
        raise SnapshotNotFoundError(
            f"Snapshot {snapshot.snapshot_id[:_SHORT_ID_LEN]} is the first "
            f"snapshot of dataset {snapshot.dataset_id}; there is nothing to "
            "compare it against. Name a second snapshot explicitly."
        )
    return _load(
        store.SnapshotLocation(dataset_id=snapshot.dataset_id, snapshot_id=parent),
        snapshot.spec,
        store_root=store_root,
    )


def _head_of(dataset_id: str, spec: str, *, store_root: Path) -> ResolvedSnapshot:
    """Return a dataset's latest snapshot.

    Args:
        dataset_id: The dataset.
        spec: What the user typed to name it, for error messages.
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        The snapshot the dataset's ``HEAD`` names.

    Raises:
        SnapshotNotFoundError: If the dataset has never been snapshotted.
    """
    head = store.read_head(dataset_id, store_root=store_root)
    if head is None:
        raise SnapshotNotFoundError(
            f"Dataset {dataset_id} has no snapshots yet, so there is nothing "
            "to compare. Run `dstrack track <path>` first."
        )
    return _load(
        store.SnapshotLocation(dataset_id=dataset_id, snapshot_id=head),
        spec,
        store_root=store_root,
    )


def _load(
    location: store.SnapshotLocation, spec: str, *, store_root: Path
) -> ResolvedSnapshot:
    """Read a located snapshot's payload.

    Args:
        location: Which dataset and snapshot to read.
        spec: What the user typed, carried through for error messages.
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        The snapshot with its payload.

    Raises:
        SnapshotNotFoundError: If the payload is not on disk.
        StoreCorruptionError: If it cannot be read back as a JSON object.
    """
    return ResolvedSnapshot(
        dataset_id=location.dataset_id,
        snapshot_id=location.snapshot_id,
        payload=store.read_snapshot(
            location.dataset_id, location.snapshot_id, store_root=store_root
        ),
        spec=spec,
    )


def _looks_like_prefix(spec: str) -> bool:
    """Return whether `spec` could be the leading characters of a snapshot id.

    Args:
        spec: The reference, as the user typed it.

    Returns:
        ``True`` if it is hex-and-dashes and of a plausible length. A shorter
        string would match almost anything, and a longer one is not a UUID at
        all.
    """
    return _MIN_PREFIX_LEN <= len(spec) <= _MAX_PREFIX_LEN and set(spec) <= _HEX_CHARS


def _match_prefix(
    prefix: str, spec: str, *, store_root: Path
) -> store.SnapshotLocation | None:
    """Find the one snapshot whose id starts with `prefix`.

    Args:
        prefix: The canonical id or leading characters to match.
        spec: What the user typed, for the error message.
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        The single match, or ``None`` if nothing matched.

    Raises:
        AmbiguousSnapshotError: If more than one snapshot matched.
    """
    matches = cache.find_snapshots_by_prefix(
        prefix, store_root=store_root, limit=_MAX_CANDIDATES
    )
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    raise AmbiguousSnapshotError(
        f"Snapshot id {spec!r} is ambiguous; it matches {len(matches)} "
        f"snapshots:\n{_candidates(matches, store_root=store_root)}\n"
        "Give more characters of the id."
    )


def _candidates(matches: Sequence[store.SnapshotLocation], *, store_root: Path) -> str:
    """Describe ambiguous matches well enough to tell them apart.

    A bare list of ids is no help in choosing between them, so each is shown
    with what its snapshot recorded. A payload that cannot be read is still
    listed by id: it is a candidate whether or not it describes itself.

    Args:
        matches: The snapshots a prefix matched.
        store_root: Path to the ``.dstrack/`` directory.

    Returns:
        One indented line per candidate.
    """
    lines = []
    for match in matches:
        try:
            payload = store.read_snapshot(
                match.dataset_id, match.snapshot_id, store_root=store_root
            )
        except (OSError, SnapshotNotFoundError, StoreCorruptionError):
            payload = {}
        name = payload.get("dataset_name") or "-"
        created = payload.get("created_at") or "-"
        lines.append(
            f"  {match.snapshot_id[:_SHORT_ID_LEN]}  {name}  {created}  "
            f"dataset {match.dataset_id}"
        )
    return "\n".join(lines)
