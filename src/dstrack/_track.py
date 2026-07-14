"""The ``dstrack track`` command: snapshot a dataset into the local store."""

import getpass
import os
from pathlib import Path
from typing import Annotated

import typer

from dstrack import console
from dstrack.errors import (
    DatasetNotFoundError,
    StoreCorruptionError,
    StoreNotFoundError,
)
from dstrack.paths import resolve_store_root
from dstrack.readers import resolve_reader
from dstrack.snapshot import SnapshotBuilder
from dstrack.store import write_snapshot


def _default_created_by() -> str:
    """Best-effort identifier for the user creating the snapshot."""
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - platform dependent
        return "unknown"


def track(
    path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            help="Path to the dataset file to snapshot.",
        ),
    ],
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help="Human-readable dataset name. Defaults to the file's stem.",
        ),
    ] = None,
    reader: Annotated[
        str | None,
        typer.Option(
            "--reader",
            help="Reader to use: a registered name (e.g. 'csv'), or "
            "'package.module:ClassName' for a reader that is not installed as a "
            "plugin. Inferred from the file extension when omitted.",
        ),
    ] = None,
    root: Annotated[
        Path | None,
        typer.Option(
            "--root",
            help="Path root the stored dataset_path is made relative to. "
            "Defaults to the store root (the directory containing .dstrack/).",
        ),
    ] = None,
    dataset_id: Annotated[
        str | None,
        typer.Option(
            "--dataset-id",
            help="Continue this dataset's lineage explicitly, instead of "
            "matching by path. Needed after a file is renamed or moved.",
        ),
    ] = None,
    created_by: Annotated[
        str | None,
        typer.Option("--created-by", help="Override the recorded author."),
    ] = None,
) -> None:
    """Compute a snapshot of a dataset and store it in the local store."""
    # Locate the .dstrack/ store; without one there is nowhere to write.
    try:
        store_root = resolve_store_root()
    except StoreNotFoundError as e:
        console.error(str(e))
        raise typer.Exit(code=1) from e

    # Record the dataset location relative to the path root, so the store stays
    # portable across machines and checkouts.
    path_root = root if root is not None else store_root.parent
    dataset_path = Path(
        os.path.relpath(path.resolve(), Path(path_root).resolve())
    ).as_posix()

    # Pick the reader: explicit --reader wins, otherwise infer from the extension.
    try:
        tabular_reader = resolve_reader(path, reader=reader)
    except (ValueError, TypeError) as e:
        hint = "" if reader is not None else " Use --reader to name one explicitly."
        console.error(f"{e}{hint}")
        raise typer.Exit(code=1) from e

    # Read the file and compute the snapshot's statistics and metadata. The
    # portable relative path is recorded; the real file on disk is what gets read
    # and hashed.
    console.info(f"Reading {path} and computing snapshot...")
    snapshot = SnapshotBuilder().build(
        tabular_reader,
        dataset_name=name or path.stem,
        dataset_path=dataset_path,
        source=path,
        source_type="file",  # TODO: Add a way to manage other sources like folders
        created_by=created_by or _default_created_by(),
    )

    # Persist the snapshot, attaching it to an existing lineage when one matches.
    try:
        result = write_snapshot(snapshot, store_root=store_root, dataset_id=dataset_id)
    except (DatasetNotFoundError, StoreCorruptionError) as e:
        console.error(str(e))
        raise typer.Exit(code=1) from e

    # Report what was written and whether it started or extended a lineage.
    lineage = "new dataset" if result.is_new_dataset else "continued lineage"
    console.success(
        f"Snapshot {result.snapshot_id} written ({lineage}, "
        f"dataset {result.dataset_id})."
    )
    console.info(f"Stored at {result.snapshot_path}")
