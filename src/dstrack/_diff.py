"""The ``dstrack diff`` command: compare two snapshots of a dataset."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from dstrack import cache, console
from dstrack._diff_render import render_diff
from dstrack._resolve import ensure_index, resolve_pair
from dstrack.diff import PRESETS, DiffOptions, SnapshotDiff, diff_snapshots, preset
from dstrack.errors import (
    AmbiguousSnapshotError,
    DatasetNotFoundError,
    IncompatibleSnapshotError,
    MissingDependencyError,
    SnapshotNotFoundError,
    StoreCorruptionError,
    StoreNotFoundError,
)
from dstrack.paths import resolve_store_root

# Failures that mean "what you asked for is not in the store, or is not
# readable", all of which are reported the same way: a message, and exit 1.
_USER_ERRORS = (
    AmbiguousSnapshotError,
    DatasetNotFoundError,
    IncompatibleSnapshotError,
    SnapshotNotFoundError,
    StoreCorruptionError,
    ValueError,
    OSError,
    sqlite3.DatabaseError,
)


def diff(
    refs: Annotated[
        list[str] | None,
        typer.Argument(
            help="Snapshots to compare: a snapshot id (in full or by its "
            "leading characters), a dataset id, or the path to a tracked "
            "dataset file. Given one, it is compared against the snapshot it "
            "succeeded; given two, the first is the baseline.",
        ),
    ] = None,
    from_ref: Annotated[
        str | None,
        typer.Option(
            "--from",
            help="Baseline to compare against. Alone, the dataset's latest "
            "snapshot is compared to it.",
        ),
    ] = None,
    to_ref: Annotated[
        str | None,
        typer.Option(
            "--to",
            help="Snapshot to compare. Alone, it is compared against the one "
            "it succeeded.",
        ),
    ] = None,
    sensitivity: Annotated[
        str,
        typer.Option(
            "--sensitivity",
            help="How much movement counts as significant: low, normal, or high.",
        ),
    ] = "normal",
    show_all: Annotated[
        bool,
        typer.Option("--all", help="Include columns and metrics that did not change."),
    ] = False,
    oneline: Annotated[
        bool,
        typer.Option("--oneline", help="Condense the comparison to a single line."),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Print the comparison as JSON instead of a table. Implies a "
            "non-interactive run, so pass `--yes` in scripts.",
        ),
    ] = False,
    pdf: Annotated[
        Path | None,
        typer.Option(
            "--pdf",
            dir_okay=False,
            writable=True,
            help="Also write a PDF report of the comparison to this path. "
            "Needs the `report` extra: pip install 'dstrack[report]'.",
        ),
    ] = None,
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
            help="Path root a REF path is made relative to, matching the one "
            "`dstrack track` recorded it with. Defaults to the store root (the "
            "directory containing .dstrack/). Ignored for a REF that is an id.",
        ),
    ] = None,
) -> None:
    """Compare two snapshots and report what changed between them."""
    options = _options(sensitivity)

    # Locate the .dstrack/ store; without one there is nothing to compare.
    try:
        store_root = resolve_store_root()
    except StoreNotFoundError as e:
        console.error(str(e))
        raise typer.Exit(code=1) from e

    # Under --json the only thing on stdout may be the document itself, so the
    # index cannot be offered interactively: without one, --yes or --rebuild
    # must be given, or the request is rejected.
    ensure_index(store_root, yes=yes, rebuild=rebuild, quiet=as_json)

    try:
        # Sync before resolving: the index does not travel with the repository,
        # so a snapshot taken elsewhere is only visible once its log is read.
        reason = cache.sync(store_root)
        if reason is not None and not as_json:
            console.warning(f"Rebuilt the snapshot index: {reason}.")

        old, new = resolve_pair(
            refs or [],
            from_ref=from_ref,
            to_ref=to_ref,
            store_root=store_root,
            root=root,
        )
        result = diff_snapshots(
            old.payload,
            new.payload,
            options=options,
            old_dataset_id=old.dataset_id,
            new_dataset_id=new.dataset_id,
        )
    except _USER_ERRORS as e:
        console.error(str(e))
        raise typer.Exit(code=1) from e

    if pdf is not None and not _write_pdf(result, pdf, quiet=as_json):
        raise typer.Exit(code=1)

    if as_json:
        # Deliberately not `console.display`: the document is data, and rich
        # would wrap and highlight it into something no parser accepts.
        print(json.dumps(result.to_dict(), indent=2))
        return

    if not result.same_dataset:
        console.warning("Comparing snapshots from two different datasets.")

    console.display(
        render_diff(
            result,
            width=console.width(),
            now=datetime.now(UTC),
            oneline=oneline,
            show_unchanged=show_all,
        )
    )


def _options(sensitivity: str) -> DiffOptions:
    """Return the thresholds a named sensitivity stands for.

    Args:
        sensitivity: The preset name, as the user gave it.

    Returns:
        Its thresholds.

    Raises:
        typer.BadParameter: If no preset goes by that name. Raised rather than
            reported, so the failure reads like any other bad option.
    """
    try:
        return preset(sensitivity)
    except KeyError as e:
        known = ", ".join(sorted(PRESETS))
        raise typer.BadParameter(
            f"Unknown sensitivity {sensitivity!r}. Choose one of: {known}."
        ) from e


def _write_pdf(result: SnapshotDiff, path: Path, *, quiet: bool) -> bool:
    """Write the PDF report, reporting a failure rather than raising it.

    The reporting module is imported here rather than at the top of the file
    because it needs an optional dependency: importing it eagerly would make
    every `dstrack diff` pay for a feature most runs do not use, and would turn
    a missing extra into an import error on an unrelated command.

    Args:
        result: The comparison to report.
        path: Where to write the PDF.
        quiet: Suppress the success message, for a run whose stdout is data.

    Returns:
        Whether the report was written.
    """
    from dstrack._diff_report import write_pdf_report

    try:
        write_pdf_report(result, path, now=datetime.now(UTC))
    except (MissingDependencyError, OSError) as e:
        console.error(str(e))
        return False

    if not quiet:
        console.success(f"Wrote the comparison report to {path}")
    return True
