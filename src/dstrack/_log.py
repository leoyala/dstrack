"""The ``dstrack log`` command: show a tracked dataset's history."""

import os
import sys
import uuid
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Annotated

import typer

from dstrack import cache, console, store
from dstrack._log_render import render_history
from dstrack.errors import (
    DatasetNotFoundError,
    StoreCorruptionError,
    StoreNotFoundError,
)
from dstrack.paths import resolve_store_root


def log(
    target: Annotated[
        str,
        typer.Argument(
            metavar="TARGET",
            help="Dataset to show the history of: either a dataset id, or the "
            "path to a tracked dataset file.",
        ),
    ],
    limit: Annotated[
        int | None,
        typer.Option(
            "-n",
            "--limit",
            min=1,
            help="Show at most this many snapshots, counting back from the latest.",
        ),
    ] = None,
    reverse: Annotated[
        bool,
        typer.Option("--reverse", help="Show oldest first instead of newest first."),
    ] = False,
    oneline: Annotated[
        bool,
        typer.Option("--oneline", help="Condense each snapshot to a single line."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "-y",
            "--yes",
            help="Build the snapshot index without asking, if it does not exist yet.",
        ),
    ] = False,
    rebuild: Annotated[
        bool,
        typer.Option(
            "--rebuild",
            help="Discard the snapshot index and rebuild it from the dataset logs.",
        ),
    ] = False,
    root: Annotated[
        Path | None,
        typer.Option(
            "--root",
            help="Path root a TARGET path is made relative to, matching the one "
            "`dstrack track` recorded it with. Defaults to the store root (the "
            "directory containing .dstrack/). Ignored when TARGET is a dataset id.",
        ),
    ] = None,
) -> None:
    """Show the snapshot history of a tracked dataset."""
    # Locate the .dstrack/ store; without one there is no history to show.
    try:
        store_root = resolve_store_root()
    except StoreNotFoundError as e:
        console.error(str(e))
        raise typer.Exit(code=1) from e

    _ensure_index(store_root, yes=yes, rebuild=rebuild)

    try:
        # Sync before resolving: the index does not travel with the repository,
        # so a dataset tracked elsewhere is only visible once its log is read.
        reason = cache.sync(store_root)
        if reason is not None:
            console.warning(f"Rebuilt the snapshot index: {reason}.")

        dataset_id = _resolve_target(target, store_root=store_root, root=root)
        history = cache.query_history(dataset_id, store_root=store_root)
    except (DatasetNotFoundError, StoreCorruptionError, ValueError) as e:
        console.error(str(e))
        raise typer.Exit(code=1) from e

    if not history:
        console.warning(f"Dataset {dataset_id} has no snapshots yet.")
        return

    # Deltas are keyed by lineage, not by display position, so that slicing and
    # reversing below cannot change what a snapshot is compared against.
    parents = {entry.snapshot_id: parent for entry, parent in pairwise(history)}
    head = history[0].snapshot_id

    # Limit counts back from HEAD, then --reverse flips what is shown; the two
    # compose the way `git log -n ... --reverse` does.
    shown = history if limit is None else history[:limit]
    if reverse:
        shown = list(reversed(shown))

    console.display(
        render_history(
            shown,
            parents=parents,
            head_id=head,
            now=datetime.now(UTC),
            oneline=oneline,
        )
    )


def _ensure_index(store_root: Path, *, yes: bool, rebuild: bool) -> None:
    """Make sure the snapshot index may be built, asking first if need be.

    Building parses every dataset's log, so it is offered rather than done
    silently. Consent is only about the first build: an index that exists but
    cannot be used is rebuilt without asking, since it holds nothing that the
    logs do not.

    Args:
        store_root: Path to the ``.dstrack/`` directory.
        yes: Consent given up front, so nothing is asked.
        rebuild: Discard any existing index, which implies consent.

    Raises:
        typer.Exit: Code 0 if the user declines to build it; code 1 if there
            is no index and no terminal to ask at.
    """
    path = cache.index_path(store_root)
    if rebuild:
        path.unlink(missing_ok=True)
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

    console.info("Building the snapshot index from the dataset logs...")


def _resolve_target(target: str, *, store_root: Path, root: Path | None) -> str:
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
    canonical = _canonical_uuid(target)
    if canonical is not None and store.dataset_exists(canonical, store_root=store_root):
        return canonical

    path_root = root if root is not None else store_root.parent
    dataset_path = Path(
        os.path.relpath(Path(target).resolve(), Path(path_root).resolve())
    ).as_posix()

    matched = cache.find_dataset_by_path(dataset_path, store_root=store_root)
    if matched is not None:
        return matched

    if canonical is not None:
        raise DatasetNotFoundError(
            f"No dataset {target!r} in the store.\n{_known_datasets(store_root)}"
        )
    raise DatasetNotFoundError(
        f"No tracked dataset at {target!r} (recorded as {dataset_path!r} "
        f"relative to {path_root}).\n"
        "If the file was renamed or moved, its recorded path no longer "
        "matches; pass the dataset id instead. Use `--root` to change the "
        "path root the recorded path is computed against.\n"
        f"{_known_datasets(store_root)}"
    )


def _canonical_uuid(value: str) -> str | None:
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


def _known_datasets(store_root: Path) -> str:
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
