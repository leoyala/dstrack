"""The ``dstrack log`` command: show a tracked dataset's history."""

import sqlite3
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Annotated

import typer

from dstrack import cache, console
from dstrack._log_render import render_history
from dstrack._resolve import ensure_index, resolve_target
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

    ensure_index(store_root, yes=yes, rebuild=rebuild)

    try:
        # Sync before resolving: the index does not travel with the repository,
        # so a dataset tracked elsewhere is only visible once its log is read.
        reason = cache.sync(store_root)
        if reason is not None:
            console.warning(f"Rebuilt the snapshot index: {reason}.")

        dataset_id = resolve_target(target, store_root=store_root, root=root)
        history = cache.query_history(dataset_id, store_root=store_root)
    except (
        DatasetNotFoundError,
        StoreCorruptionError,
        ValueError,
        OSError,
        sqlite3.DatabaseError,
    ) as e:
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
