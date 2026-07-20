"""Tests for the `dstrack log` command in src/dstrack/_log.py."""

import json
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from dstrack import _log, cache
from dstrack._cli import app
from dstrack._log import (
    _canonical_uuid,
    _ensure_index,
    _known_datasets,
    _resolve_target,
)
from dstrack.errors import DatasetNotFoundError, StoreCorruptionError

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def store_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an empty local store under a fresh cwd and return its root.

    `log` resolves the store by walking up from the current directory, so the
    tests run from inside `tmp_path`. `DSTRACK_ROOT_PATH` is cleared so an
    ambient value cannot redirect the store elsewhere.
    """
    monkeypatch.delenv("DSTRACK_ROOT_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ".dstrack"
    (root / "datasets").mkdir(parents=True)
    return root


def _write_csv(path: Path, rows: int = 3) -> Path:
    """Write a small, valid CSV file and return its path."""
    lines = ["a,b"] + [f"{i},{i * 2}" for i in range(rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _track(root: Path, path: Path, *args: str) -> str:
    """Track `path` through the CLI and return its dataset id."""
    result = runner.invoke(app, ["track", str(path), *args])
    assert result.exit_code == 0, result.output
    return _dataset_id_of(root, path)


def _dataset_id_of(root: Path, path: Path) -> str:
    """Return the id of the dataset whose snapshots recorded `path`."""
    for snapshot in root.glob("datasets/*/snapshots/*.json"):
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        if Path(payload["dataset_path"]).name == path.name:
            return str(snapshot.parent.parent.name)
    raise AssertionError(f"no dataset recorded for {path}")


def _pretend_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `_log` see a terminal on stdin, so it offers to build the index.

    `CliRunner` installs its own `sys.stdin` for the duration of an invocation,
    which would undo a patch of the real one; the module's own reference to
    `sys` is replaced instead. Only `stdin.isatty` is ever read through it.
    """
    monkeypatch.setattr(
        _log, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True))
    )


def _log_cmd(*args: str) -> object:
    """Invoke `dstrack log` with `--yes`, so the index is built unprompted."""
    return runner.invoke(app, ["log", *args, "--yes"])


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_log_by_path_shows_the_snapshot(tmp_path: Path, store_root: Path) -> None:
    """A tracked file's path resolves to its dataset and shows its history."""
    csv = _write_csv(tmp_path / "data.csv")
    _track(store_root, csv)

    result = _log_cmd(str(csv))

    assert result.exit_code == 0, result.output
    assert "data" in result.output
    assert "3 rows" in result.output
    assert "HEAD" in result.output


def test_log_by_dataset_id_shows_the_snapshot(tmp_path: Path, store_root: Path) -> None:
    """A dataset id resolves without touching the filesystem path at all."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    csv.unlink()  # The id must work even once the file is gone.

    result = _log_cmd(dataset_id)

    assert result.exit_code == 0, result.output
    assert "3 rows" in result.output


def test_log_shows_newest_first(tmp_path: Path, store_root: Path) -> None:
    """Without --reverse the latest snapshot is marked HEAD and comes first."""
    csv = _write_csv(tmp_path / "data.csv", rows=3)
    _track(store_root, csv)
    _write_csv(csv, rows=5)
    _track(store_root, csv)

    result = _log_cmd(str(csv))

    assert result.exit_code == 0, result.output
    head_line = next(line for line in result.output.splitlines() if "HEAD" in line)
    assert result.output.splitlines().index(head_line) < 4
    # The newest snapshot gained two rows over its parent.
    assert "+2" in result.output


def test_log_reverse_shows_oldest_first(tmp_path: Path, store_root: Path) -> None:
    """--reverse flips display order but keeps HEAD marked on the latest."""
    csv = _write_csv(tmp_path / "data.csv", rows=3)
    _track(store_root, csv)
    _write_csv(csv, rows=5)
    _track(store_root, csv)

    result = _log_cmd(str(csv), "--reverse")

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    head_index = next(i for i, line in enumerate(lines) if "HEAD" in line)
    assert head_index > 3, result.output


def test_log_limit_counts_back_from_head(tmp_path: Path, store_root: Path) -> None:
    """--limit keeps the newest N snapshots, dropping the older ones."""
    csv = _write_csv(tmp_path / "data.csv", rows=3)
    _track(store_root, csv)
    _write_csv(csv, rows=5)
    _track(store_root, csv)
    dataset_id = _dataset_id_of(store_root, csv)
    cache.sync(store_root)
    history = cache.query_history(dataset_id, store_root=store_root)

    result = _log_cmd(str(csv), "-n", "1")

    assert result.exit_code == 0, result.output
    assert history[0].snapshot_id[:8] in result.output
    assert history[1].snapshot_id[:8] not in result.output


def test_log_limit_and_reverse_compose(tmp_path: Path, store_root: Path) -> None:
    """--limit selects from HEAD first; --reverse only reorders what remains."""
    csv = _write_csv(tmp_path / "data.csv", rows=3)
    _track(store_root, csv)
    _write_csv(csv, rows=5)
    _track(store_root, csv)
    dataset_id = _dataset_id_of(store_root, csv)
    cache.sync(store_root)
    history = cache.query_history(dataset_id, store_root=store_root)

    result = _log_cmd(str(csv), "-n", "1", "--reverse")

    assert result.exit_code == 0, result.output
    assert history[0].snapshot_id[:8] in result.output
    assert history[1].snapshot_id[:8] not in result.output


def test_log_limit_below_one_is_rejected(tmp_path: Path, store_root: Path) -> None:
    """--limit is constrained to at least one snapshot."""
    csv = _write_csv(tmp_path / "data.csv")
    _track(store_root, csv)

    result = _log_cmd(str(csv), "-n", "0")

    assert result.exit_code == 2, result.output


def test_log_oneline_condenses_each_snapshot(tmp_path: Path, store_root: Path) -> None:
    """--oneline renders one line per snapshot instead of a detail block."""
    csv = _write_csv(tmp_path / "data.csv")
    _track(store_root, csv)

    detailed = _log_cmd(str(csv))
    condensed = _log_cmd(str(csv), "--oneline")

    assert condensed.exit_code == 0, condensed.output
    assert len(condensed.output.splitlines()) < len(detailed.output.splitlines())
    assert "3 rows" in condensed.output
    # The path is a detail-view line only.
    assert "data.csv" not in condensed.output


def test_log_root_option_changes_the_path_root(
    tmp_path: Path, store_root: Path
) -> None:
    """--root matches the path root `track` recorded the dataset with."""
    nested = tmp_path / "nested"
    nested.mkdir()
    csv = _write_csv(nested / "data.csv")
    _track(store_root, csv, "--root", str(nested))

    without_root = _log_cmd(str(csv))
    with_root = _log_cmd(str(csv), "--root", str(nested))

    assert without_root.exit_code == 1, without_root.output
    assert with_root.exit_code == 0, with_root.output


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_log_without_a_store_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no `.dstrack/` anywhere above the cwd there is no history to show."""
    monkeypatch.delenv("DSTRACK_ROOT_PATH", raising=False)
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    result = _log_cmd("whatever")

    assert result.exit_code == 1, result.output


def test_log_unknown_dataset_id_lists_known_datasets(
    tmp_path: Path, store_root: Path
) -> None:
    """An id no dataset goes by fails, and the error names the ids that exist."""
    csv = _write_csv(tmp_path / "data.csv")
    known = _track(store_root, csv)

    result = _log_cmd(str(uuid.uuid4()))

    assert result.exit_code == 1, result.output
    assert "No dataset" in result.output
    assert known[:8] in result.output


def test_log_untracked_path_explains_the_recorded_path(
    tmp_path: Path, store_root: Path
) -> None:
    """A path no dataset recorded reports what it was looked up as."""
    _write_csv(tmp_path / "data.csv")

    result = _log_cmd(str(tmp_path / "data.csv"))

    assert result.exit_code == 1, result.output
    assert "No tracked dataset" in result.output
    assert "Known datasets" in result.output


def test_log_dataset_without_snapshots_warns(tmp_path: Path, store_root: Path) -> None:
    """A dataset directory with no committed snapshot has nothing to show."""
    dataset_id = str(uuid.uuid4())
    (store_root / "datasets" / dataset_id).mkdir()

    result = _log_cmd(dataset_id)

    assert result.exit_code == 0, result.output
    assert "no snapshots yet" in result.output


# ---------------------------------------------------------------------------
# Index building (`_ensure_index`)
# ---------------------------------------------------------------------------


def test_log_without_yes_and_without_a_terminal_exits_one(
    tmp_path: Path, store_root: Path
) -> None:
    """With no index and no tty to ask at, the command says to pass --yes."""
    csv = _write_csv(tmp_path / "data.csv")
    _track(store_root, csv)

    result = runner.invoke(app, ["log", str(csv)])

    assert result.exit_code == 1, result.output
    assert "--yes" in result.output
    assert not cache.index_exists(store_root)


def test_log_prompts_and_builds_when_accepted(
    tmp_path: Path, store_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At a terminal the build is offered, and accepting it shows the history."""
    csv = _write_csv(tmp_path / "data.csv")
    _track(store_root, csv)
    _pretend_terminal(monkeypatch)

    result = runner.invoke(app, ["log", str(csv)], input="y\n")

    assert result.exit_code == 0, result.output
    assert "3 rows" in result.output
    assert cache.index_exists(store_root)


def test_log_prompt_declined_exits_zero_without_building(
    tmp_path: Path, store_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declining the build is a choice, not an error, and leaves no index."""
    csv = _write_csv(tmp_path / "data.csv")
    _track(store_root, csv)
    _pretend_terminal(monkeypatch)

    result = runner.invoke(app, ["log", str(csv)], input="n\n")

    assert result.exit_code == 0, result.output
    assert "nothing to show" in result.output
    assert not cache.index_exists(store_root)


def test_log_existing_index_is_not_offered_again(
    tmp_path: Path, store_root: Path
) -> None:
    """Once an index exists, no consent is needed to read it."""
    csv = _write_csv(tmp_path / "data.csv")
    _track(store_root, csv)
    assert _log_cmd(str(csv)).exit_code == 0

    result = runner.invoke(app, ["log", str(csv)])

    assert result.exit_code == 0, result.output
    assert "no snapshot index" not in result.output.lower()


def test_ensure_index_rebuild_discards_the_existing_index(
    tmp_path: Path, store_root: Path
) -> None:
    """--rebuild deletes the index without asking, implying consent."""
    csv = _write_csv(tmp_path / "data.csv")
    _track(store_root, csv)
    assert _log_cmd(str(csv)).exit_code == 0
    assert cache.index_exists(store_root)

    _ensure_index(store_root, yes=False, rebuild=True)

    assert not cache.index_exists(store_root)


def test_ensure_index_rebuild_on_a_missing_index_is_fine(store_root: Path) -> None:
    """--rebuild does not require an index to already be there."""
    _ensure_index(store_root, yes=False, rebuild=True)

    assert not cache.index_exists(store_root)


def test_log_rebuild_still_shows_the_history(tmp_path: Path, store_root: Path) -> None:
    """The index is rebuilt from the dataset logs, so nothing is lost."""
    csv = _write_csv(tmp_path / "data.csv")
    _track(store_root, csv)
    assert _log_cmd(str(csv)).exit_code == 0

    result = runner.invoke(app, ["log", str(csv), "--rebuild"])

    assert result.exit_code == 0, result.output
    assert "3 rows" in result.output


def test_ensure_index_without_a_terminal_raises_exit_one(store_root: Path) -> None:
    """The no-tty branch exits rather than blocking on a prompt."""
    with pytest.raises(typer.Exit) as excinfo:
        _ensure_index(store_root, yes=False, rebuild=False)

    assert excinfo.value.exit_code == 1


# ---------------------------------------------------------------------------
# Target resolution (`_resolve_target`, `_canonical_uuid`)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "spell"),
    [
        ("canonical", lambda v: v),
        ("uppercased", str.upper),
        ("braced", lambda v: f"{{{v}}}"),
        ("dashless", lambda v: v.replace("-", "")),
        ("urn", lambda v: f"urn:uuid:{v}"),
    ],
)
def test_canonical_uuid_accepts_alternate_spellings(
    label: str, spell: Callable[[str], str]
) -> None:
    """Uppercased, braced, dashless and urn ids all name the same dataset."""
    value = str(uuid.uuid4())

    assert _canonical_uuid(spell(value)) == value


@pytest.mark.parametrize("value", ["", "data.csv", "not-a-uuid", "../etc/passwd"])
def test_canonical_uuid_rejects_non_uuids(value: str) -> None:
    """Anything that is not a UUID in some spelling is not an id."""
    assert _canonical_uuid(value) is None


def test_resolve_target_prefers_an_existing_id_over_a_path(
    tmp_path: Path, store_root: Path
) -> None:
    """An id that names a real dataset wins, so no file can shadow it."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    cache.sync(store_root)
    # A file in the cwd named exactly like the id must not be picked up.
    _write_csv(tmp_path / dataset_id)

    resolved = _resolve_target(dataset_id, store_root=store_root, root=None)

    assert resolved == dataset_id


def test_resolve_target_falls_back_to_a_path(tmp_path: Path, store_root: Path) -> None:
    """A target that is not an existing id is read as a path."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    cache.sync(store_root)

    assert _resolve_target(str(csv), store_root=store_root, root=None) == dataset_id


def test_resolve_target_relative_path_matches(tmp_path: Path, store_root: Path) -> None:
    """A relative path resolves the same way an absolute one does."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    cache.sync(store_root)

    assert _resolve_target("data.csv", store_root=store_root, root=None) == dataset_id


def test_resolve_target_unknown_id_reports_the_id(store_root: Path) -> None:
    """A well-formed id no dataset goes by is reported as a missing dataset."""
    cache.sync(store_root)
    missing = str(uuid.uuid4())

    with pytest.raises(DatasetNotFoundError, match="No dataset") as excinfo:
        _resolve_target(missing, store_root=store_root, root=None)

    assert missing in str(excinfo.value)


def test_resolve_target_unknown_path_suggests_the_id(store_root: Path) -> None:
    """A path no dataset recorded explains how to recover with a dataset id."""
    cache.sync(store_root)

    with pytest.raises(DatasetNotFoundError, match="No tracked dataset") as excinfo:
        _resolve_target("missing.csv", store_root=store_root, root=None)

    message = str(excinfo.value)
    assert "missing.csv" in message


def test_resolve_target_honours_an_explicit_root(
    tmp_path: Path, store_root: Path
) -> None:
    """`root` replaces the store root as what the path is made relative to."""
    nested = tmp_path / "nested"
    nested.mkdir()
    csv = _write_csv(nested / "data.csv")
    dataset_id = _track(store_root, csv, "--root", str(nested))
    cache.sync(store_root)

    assert _resolve_target(str(csv), store_root=store_root, root=nested) == dataset_id
    with pytest.raises(DatasetNotFoundError):
        _resolve_target(str(csv), store_root=store_root, root=None)


# ---------------------------------------------------------------------------
# Error listing (`_known_datasets`)
# ---------------------------------------------------------------------------


def test_known_datasets_on_an_empty_store(store_root: Path) -> None:
    """An empty store says so, and points at the command that fills it."""
    cache.sync(store_root)

    listing = _known_datasets(store_root)

    assert "(none)" in listing
    assert "dstrack track" in listing


def test_known_datasets_lists_id_name_and_path(
    tmp_path: Path, store_root: Path
) -> None:
    """Each dataset is shown with what its latest snapshot recorded."""
    csv = _write_csv(tmp_path / "customers.csv")
    dataset_id = _track(store_root, csv, "--name", "clients")
    cache.sync(store_root)

    listing = _known_datasets(store_root)

    assert dataset_id in listing
    assert "clients" in listing
    assert "customers.csv" in listing


def test_known_datasets_marks_missing_fields(store_root: Path) -> None:
    """A dataset with no committed snapshot still appears, with dashes."""
    dataset_id = str(uuid.uuid4())
    (store_root / "datasets" / dataset_id).mkdir()
    cache.sync(store_root)

    listing = _known_datasets(store_root)

    assert dataset_id in listing
    assert "-" in listing


def test_known_datasets_drops_a_dataset_deleted_from_the_store(
    tmp_path: Path, store_root: Path
) -> None:
    """A dataset removed from the store is forgotten by the next sync.

    The index outlives the logs it was built from, so a dataset directory
    deleted by hand would otherwise keep being listed as if it existed.
    """
    removed_id = _track(store_root, _write_csv(tmp_path / "gone.csv"))
    kept_id = _track(store_root, _write_csv(tmp_path / "kept.csv"))
    cache.sync(store_root)
    assert removed_id in _known_datasets(store_root)

    shutil.rmtree(store_root / "datasets" / removed_id)
    cache.sync(store_root)

    listing = _known_datasets(store_root)
    assert removed_id not in listing
    assert kept_id in listing


def test_log_reports_a_rebuilt_index(
    tmp_path: Path, store_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sync that had to rebuild the index says why, and still shows history."""
    csv = _write_csv(tmp_path / "data.csv")
    _track(store_root, csv)
    assert _log_cmd(str(csv)).exit_code == 0
    monkeypatch.setattr(cache, "sync", lambda root: "the schema changed")

    result = _log_cmd(str(csv))

    assert result.exit_code == 0, result.output
    assert "Rebuilt the snapshot index" in result.output


def test_log_surfaces_a_corrupt_store(
    tmp_path: Path, store_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store-level failure is reported as an error, not a traceback."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)

    def _boom(*args: object, **kwargs: object) -> bool:
        raise StoreCorruptionError("log.jsonl is not readable")

    monkeypatch.setattr(_log.store, "dataset_exists", _boom)

    result = _log_cmd(dataset_id)

    assert result.exit_code == 1, result.output
    assert "not readable" in result.output
