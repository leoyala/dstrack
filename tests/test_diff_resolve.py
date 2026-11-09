"""Tests for reference resolution in src/dstrack/_resolve.py and the store reads it uses."""

import json
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dstrack import cache, store
from dstrack._cli import app
from dstrack._resolve import resolve_pair, resolve_snapshot_ref
from dstrack.errors import (
    AmbiguousSnapshotError,
    DatasetNotFoundError,
    SnapshotNotFoundError,
    StoreCorruptionError,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def store_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an empty local store under a fresh cwd and return its root."""
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


def _synced(root: Path) -> Path:
    """Bring the index up to date, so prefix and path lookups can see the store."""
    cache.sync(root)
    return root


def _resolve(spec: str, root: Path) -> object:
    """Resolve one reference against a synced store."""
    return resolve_snapshot_ref(spec, store_root=_synced(root), root=None)


def _head(root: Path, dataset_id: str) -> str:
    """Return a dataset's latest snapshot id."""
    head = store.read_head(dataset_id, store_root=root)
    assert head is not None
    return head


# ---------------------------------------------------------------------------
# store.snapshot_path and store.read_snapshot
# ---------------------------------------------------------------------------


def test_read_snapshot_returns_what_was_written(
    tmp_path: Path, store_root: Path
) -> None:
    """A stored payload round-trips through the read API unchanged."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    snapshot_id = _head(store_root, dataset_id)

    payload = store.read_snapshot(dataset_id, snapshot_id, store_root=store_root)

    assert payload["snapshot_id"] == snapshot_id
    assert payload["dataset_name"] == "data"
    assert payload["num_rows"] == 3


@pytest.mark.parametrize(
    "snapshot_id", ["../../escape", "nested/id", "/absolute", "../sibling"]
)
def test_a_traversing_snapshot_id_is_refused(
    store_root: Path, snapshot_id: str
) -> None:
    """An id must name one file directly inside the dataset's snapshots/.

    A bare ``..`` is deliberately not in this list: the ``.json`` suffix makes
    it the ordinary filename ``...json``, which stays inside the directory and
    is therefore nothing to refuse.
    """
    with pytest.raises(ValueError, match="snapshot_id"):
        store.snapshot_path(str(uuid.uuid4()), snapshot_id, store_root=store_root)


def test_reading_a_snapshot_that_is_not_there_says_so(store_root: Path) -> None:
    """A missing payload is reported as missing, not as corruption."""
    dataset_id = str(uuid.uuid4())
    (store_root / "datasets" / dataset_id / "snapshots").mkdir(parents=True)

    with pytest.raises(SnapshotNotFoundError, match="No snapshot"):
        store.read_snapshot(dataset_id, str(uuid.uuid4()), store_root=store_root)


@pytest.mark.parametrize(
    ("content", "message"),
    [("[1, 2, 3]", "JSON object"), ("{oops", "not valid JSON")],
)
def test_a_damaged_payload_is_corruption(
    store_root: Path, content: str, message: str
) -> None:
    """The store wrote an object; anything else means the file was damaged."""
    dataset_id = str(uuid.uuid4())
    snapshot_id = str(uuid.uuid4())
    snapshots = store_root / "datasets" / dataset_id / "snapshots"
    snapshots.mkdir(parents=True)
    (snapshots / f"{snapshot_id}.json").write_text(content, encoding="utf-8")

    with pytest.raises(StoreCorruptionError, match=message):
        store.read_snapshot(dataset_id, snapshot_id, store_root=store_root)


# ---------------------------------------------------------------------------
# cache.find_snapshots_by_prefix
# ---------------------------------------------------------------------------


def test_a_prefix_finds_the_snapshot_and_the_dataset_holding_it(
    tmp_path: Path, store_root: Path
) -> None:
    """A snapshot id says nothing about its dataset, so the lookup returns both."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    snapshot_id = _head(store_root, dataset_id)

    matches = cache.find_snapshots_by_prefix(
        snapshot_id[:8], store_root=_synced(store_root)
    )

    assert len(matches) == 1
    assert matches[0].dataset_id == dataset_id
    assert matches[0].snapshot_id == snapshot_id


@pytest.mark.parametrize("prefix", ["____", "%", "a_b%"])
def test_like_metacharacters_in_a_prefix_match_nothing(
    tmp_path: Path, store_root: Path, prefix: str
) -> None:
    """An unescaped `_` is a wildcard and would match ids the user never named."""
    _track(store_root, _write_csv(tmp_path / "data.csv"))

    matches = cache.find_snapshots_by_prefix(prefix, store_root=_synced(store_root))

    assert matches == []


# ---------------------------------------------------------------------------
# resolve_snapshot_ref
# ---------------------------------------------------------------------------


def test_a_full_snapshot_id_resolves_to_that_snapshot(
    tmp_path: Path, store_root: Path
) -> None:
    """The most explicit reference there is."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    snapshot_id = _head(store_root, dataset_id)

    resolved = _resolve(snapshot_id, store_root)

    assert resolved.snapshot_id == snapshot_id
    assert resolved.dataset_id == dataset_id
    assert resolved.payload["snapshot_id"] == snapshot_id


def test_a_dataset_id_resolves_to_its_latest_snapshot(
    tmp_path: Path, store_root: Path
) -> None:
    """Naming a dataset means naming where it stands now."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    _write_csv(csv, rows=5)
    _track(store_root, csv)

    resolved = _resolve(dataset_id, store_root)

    assert resolved.snapshot_id == _head(store_root, dataset_id)
    assert resolved.payload["num_rows"] == 5


@pytest.mark.parametrize("spelling", ["upper", "nodashes", "braced"])
def test_an_id_is_recognized_however_it_was_pasted(
    tmp_path: Path, store_root: Path, spelling: str
) -> None:
    """Uppercase, brace-wrapped and dash-free spellings all name the same thing."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    snapshot_id = _head(store_root, dataset_id)
    variants = {
        "upper": snapshot_id.upper(),
        "nodashes": snapshot_id.replace("-", ""),
        "braced": f"{{{snapshot_id}}}",
    }

    resolved = _resolve(variants[spelling], store_root)

    assert resolved.snapshot_id == snapshot_id


def test_a_short_prefix_resolves_like_a_short_commit_hash(
    tmp_path: Path, store_root: Path
) -> None:
    """Nobody types a UUID in full."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    snapshot_id = _head(store_root, dataset_id)

    resolved = _resolve(snapshot_id[:8], store_root)

    assert resolved.snapshot_id == snapshot_id


def _no_relative_path(*_args: object, **_kwargs: object) -> str:
    """Fail the way `os.path.relpath` does across Windows drives."""
    raise ValueError("path is on mount 'D:', start on mount 'C:'")


def test_a_prefix_resolves_when_no_relative_path_exists(
    tmp_path: Path, store_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path root on another drive must not break short ids."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    snapshot_id = _head(store_root, dataset_id)
    monkeypatch.setattr("dstrack._resolve.recorded_path", _no_relative_path)

    resolved = _resolve(snapshot_id[:8], store_root)

    assert resolved.snapshot_id == snapshot_id


def test_a_path_conversion_error_surfaces_when_no_prefix_matches(
    store_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With neither reading available, the drive mismatch is the real failure."""
    monkeypatch.setattr("dstrack._resolve.recorded_path", _no_relative_path)

    with pytest.raises(ValueError, match="mount"):
        _resolve("not-a-snapshot.csv", store_root)


def test_an_ambiguous_prefix_lists_what_it_matched(
    tmp_path: Path, store_root: Path
) -> None:
    """A prefix matching several ids must show which, so one can be chosen."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    snapshot_id = _head(store_root, dataset_id)

    # Plant a second snapshot sharing the first four characters of its id.
    twin_id = snapshot_id[:4] + str(uuid.uuid4())[4:]
    payload = store.read_snapshot(dataset_id, snapshot_id, store_root=store_root)
    payload["snapshot_id"] = twin_id
    payload["parent_snapshot_id"] = snapshot_id
    snapshots = store_root / "datasets" / dataset_id / "snapshots"
    (snapshots / f"{twin_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    log = store_root / "datasets" / dataset_id / "log.jsonl"
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({**payload, "snapshot_id": twin_id}) + "\n")
    (store_root / "datasets" / dataset_id / "HEAD").write_text(
        f"{twin_id}\n", encoding="utf-8"
    )

    with pytest.raises(AmbiguousSnapshotError, match="ambiguous"):
        _resolve(snapshot_id[:4], store_root)


def test_a_prefix_shorter_than_the_minimum_is_not_treated_as_one(
    tmp_path: Path, store_root: Path
) -> None:
    """Three characters match almost anything and are far more likely a path."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    prefix = _head(store_root, dataset_id)[:3]

    with pytest.raises(DatasetNotFoundError):
        _resolve(prefix, store_root)


def test_a_tracked_path_resolves_to_its_latest_snapshot(
    tmp_path: Path, store_root: Path
) -> None:
    """The reference a user reaches for first."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)

    resolved = _resolve(str(csv), store_root)

    assert resolved.dataset_id == dataset_id


def test_a_tracked_file_that_was_deleted_still_resolves(
    tmp_path: Path, store_root: Path
) -> None:
    """History outlives the file, exactly as it does for `dstrack log`."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    csv.unlink()

    resolved = _resolve(str(csv), store_root)

    assert resolved.dataset_id == dataset_id


def test_an_unknown_reference_lists_the_store_s_datasets(
    tmp_path: Path, store_root: Path
) -> None:
    """An error a reader can act on names what does exist."""
    _track(store_root, _write_csv(tmp_path / "data.csv"))

    with pytest.raises(DatasetNotFoundError, match="Known datasets"):
        _resolve(str(tmp_path / "nope.csv"), store_root)


def test_a_dataset_with_no_snapshots_says_so(store_root: Path) -> None:
    """An empty dataset directory is not an error, but it cannot be compared."""
    dataset_id = str(uuid.uuid4())
    (store_root / "datasets" / dataset_id).mkdir(parents=True)

    with pytest.raises(SnapshotNotFoundError, match="no snapshots"):
        _resolve(dataset_id, store_root)


# ---------------------------------------------------------------------------
# resolve_pair
# ---------------------------------------------------------------------------


def _pair(
    root: Path,
    specs: list[str],
    *,
    from_ref: str | None = None,
    to_ref: str | None = None,
) -> tuple[object, object]:
    """Resolve both sides against a synced store."""
    return resolve_pair(
        specs,
        from_ref=from_ref,
        to_ref=to_ref,
        store_root=_synced(root),
        root=None,
    )


def test_one_reference_compares_a_snapshot_against_its_parent(
    tmp_path: Path, store_root: Path
) -> None:
    """The question `dstrack diff` is usually asked."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    first = _head(store_root, dataset_id)
    _write_csv(csv, rows=7)
    _track(store_root, csv)
    second = _head(store_root, dataset_id)

    old, new = _pair(store_root, [str(csv)])

    assert old.snapshot_id == first
    assert new.snapshot_id == second


def test_the_first_snapshot_of_a_dataset_has_nothing_to_compare_against(
    tmp_path: Path, store_root: Path
) -> None:
    """There is no parent, and saying so beats comparing against nothing."""
    csv = _write_csv(tmp_path / "data.csv")
    _track(store_root, csv)

    with pytest.raises(SnapshotNotFoundError, match="first snapshot"):
        _pair(store_root, [str(csv)])


def test_two_references_are_taken_in_the_order_given(
    tmp_path: Path, store_root: Path
) -> None:
    """The first argument is the baseline."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    first = _head(store_root, dataset_id)
    _write_csv(csv, rows=7)
    _track(store_root, csv)
    second = _head(store_root, dataset_id)

    old, new = _pair(store_root, [first, second])

    assert (old.snapshot_id, new.snapshot_id) == (first, second)


def test_from_alone_compares_against_where_the_dataset_stands_now(
    tmp_path: Path, store_root: Path
) -> None:
    """Naming only a baseline asks what has happened since."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    first = _head(store_root, dataset_id)
    _write_csv(csv, rows=7)
    _track(store_root, csv)

    old, new = _pair(store_root, [], from_ref=first)

    assert old.snapshot_id == first
    assert new.snapshot_id == _head(store_root, dataset_id)


def test_to_alone_compares_against_the_snapshot_it_succeeded(
    tmp_path: Path, store_root: Path
) -> None:
    """Naming only a later snapshot asks what that snapshot changed."""
    csv = _write_csv(tmp_path / "data.csv")
    dataset_id = _track(store_root, csv)
    first = _head(store_root, dataset_id)
    _write_csv(csv, rows=7)
    _track(store_root, csv)
    second = _head(store_root, dataset_id)

    old, new = _pair(store_root, [], to_ref=second)

    assert (old.snapshot_id, new.snapshot_id) == (first, second)


def test_two_datasets_may_be_compared(tmp_path: Path, store_root: Path) -> None:
    """Comparing across datasets is a supported use, not an error."""
    a = _write_csv(tmp_path / "a.csv")
    b = _write_csv(tmp_path / "b.csv", rows=9)
    dataset_a = _track(store_root, a)
    dataset_b = _track(store_root, b)

    old, new = _pair(store_root, [str(a), str(b)])

    assert old.dataset_id == dataset_a
    assert new.dataset_id == dataset_b
