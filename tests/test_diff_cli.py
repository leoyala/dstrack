"""Tests for the `dstrack diff` command in src/dstrack/_diff.py."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dstrack import cache, store
from dstrack._cli import app

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


def _write_csv(path: Path, rows: int = 3, extra_column: bool = False) -> Path:
    """Write a small, valid CSV file and return its path."""
    header = "a,b,c" if extra_column else "a,b"
    lines = [header] + [
        f"{i},{i * 2}" + (f",{i * 3}" if extra_column else "") for i in range(rows)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _track(path: Path) -> None:
    """Track `path` through the CLI."""
    result = runner.invoke(app, ["track", str(path)])
    assert result.exit_code == 0, result.output


def _dataset_id_of(root: Path, path: Path) -> str:
    """Return the id of the dataset whose snapshots recorded `path`."""
    for snapshot in root.glob("datasets/*/snapshots/*.json"):
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        if Path(payload["dataset_path"]).name == path.name:
            return str(snapshot.parent.parent.name)
    raise AssertionError(f"no dataset recorded for {path}")


@pytest.fixture
def tracked(tmp_path: Path, store_root: Path) -> Path:
    """Track one CSV twice, so it has a snapshot and a parent.

    The index is built here rather than by the first command under test, so
    that no test's output depends on whether it happened to run first.
    """
    csv = _write_csv(tmp_path / "data.csv")
    _track(csv)
    _write_csv(csv, rows=9, extra_column=True)
    _track(csv)
    cache.sync(store_root)
    return csv


def _diff(*args: str, width: str = "160") -> object:
    """Invoke `dstrack diff` with `--yes`, at a fixed terminal width."""
    return runner.invoke(app, ["diff", *args, "--yes"], env={"COLUMNS": width})


def _snapshots(root: Path, csv: Path) -> list[str]:
    """Return a dataset's snapshot ids, oldest first."""
    dataset_id = _dataset_id_of(root, csv)
    entries = store.read_log_entries(dataset_id, store_root=root)
    return [entry.snapshot_id for entry in entries]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_a_path_compares_the_latest_snapshot_to_its_parent(
    store_root: Path, tracked: Path
) -> None:
    """The default question: what changed in the last snapshot of this file?"""
    result = _diff(str(tracked))

    first, second = _snapshots(store_root, tracked)
    assert result.exit_code == 0, result.output
    assert first[:8] in result.output
    assert second[:8] in result.output
    assert "+6" in result.output, "6 rows should have been added"


def test_two_snapshot_ids_compare_in_the_order_given(
    store_root: Path, tracked: Path
) -> None:
    """Naming both sides explicitly is the unambiguous spelling."""
    first, second = _snapshots(store_root, tracked)

    forward = _diff(first, second)
    backward = _diff(second, first)

    assert forward.exit_code == 0, forward.output
    assert backward.exit_code == 0, backward.output
    assert "+6" in forward.output, "first -> second adds 6 rows"
    assert "-6" in backward.output, "second -> first removes 6 rows"


def test_short_prefixes_work_like_short_commit_hashes(
    store_root: Path, tracked: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody types a UUID in full so it should be allowed to pass the beginning of the UUID."""
    first, second = _snapshots(store_root, tracked)

    # The output shows snapshot ages ("3 seconds ago"). Freeze the clock so a
    # second boundary between the two invocations cannot make them differ.
    frozen = datetime.now(UTC)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr("dstrack._diff.datetime", _FrozenDatetime)

    result_short_uid = _diff(first[:8], second[:8])
    result_long_uid = _diff(first, second)

    assert result_short_uid.exit_code == 0, result_short_uid.output
    assert result_short_uid.output == result_long_uid.output, (
        "comparison should be identical"
    )


def test_from_and_to_name_the_two_sides(store_root: Path, tracked: Path) -> None:
    """The explicit spelling, for when the order matters and should be obvious."""
    first, second = _snapshots(store_root, tracked)

    forward = _diff("--from", first, "--to", second)
    backward = _diff("--from", second, "--to", first)

    assert forward.exit_code == 0, forward.output
    assert backward.exit_code == 0, backward.output
    assert "+6" in forward.output, "--from first --to second adds 6 rows"
    assert "-6" in backward.output, "--from second --to first removes 6 rows"


def test_to_alone_compares_against_the_snapshot_it_succeeded(
    store_root: Path, tracked: Path
) -> None:
    """Naming only the later side asks what that snapshot changed."""
    first, second = _snapshots(store_root, tracked)

    result = _diff("--to", second)

    assert result.exit_code == 0, result.output
    assert first[:8] in result.output
    assert second[:8] in result.output
    assert result.output.index(first[:8]) < result.output.index(second[:8]), (
        "the parent should be the 'before' side"
    )
    assert "+6 rows" in result.output


def test_from_alone_compares_against_where_the_dataset_stands_now(
    store_root: Path, tracked: Path
) -> None:
    """Naming only the baseline asks what has happened since."""
    first, second = _snapshots(store_root, tracked)

    result = _diff("--from", first)

    assert result.exit_code == 0, result.output
    assert first[:8] in result.output
    assert second[:8] in result.output
    assert result.output.index(first[:8]) < result.output.index(second[:8]), (
        "the baseline should be the 'before' side and the latest the 'after'"
    )
    assert "+6 rows" in result.output


def test_oneline_condenses_the_comparison(store_root: Path, tracked: Path) -> None:
    """The form that fits a CI log line."""
    result = _diff(str(tracked), "--oneline")

    assert result.exit_code == 0, result.output
    assert len(result.output.strip().splitlines()) == 1


def test_a_narrow_terminal_stacks_the_columns(store_root: Path, tracked: Path) -> None:
    """The layout follows the terminal it is printed to."""
    result = _diff(str(tracked), width="60")

    assert result.exit_code == 0, result.output
    assert max(len(line) for line in result.output.splitlines()) <= 60


def test_all_includes_what_did_not_change(store_root: Path, tracked: Path) -> None:
    """`--all` is for reading the whole picture."""
    default = _diff(str(tracked))
    everything = _diff(str(tracked), "--all")

    assert everything.exit_code == 0, everything.output
    assert len(everything.output.splitlines()) > len(default.output.splitlines())


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def test_json_output_parses_and_carries_its_version(
    store_root: Path, tracked: Path
) -> None:
    """A pipeline has to be able to read stdout with `json.loads`."""
    result = _diff(str(tracked), "--json")

    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert "diff_format_version" in document
    assert "schema" in document
    assert document["summary"]["row_delta"] == 6


def test_json_output_carries_no_decoration(store_root: Path, tracked: Path) -> None:
    """Panels, box drawing and progress notes would all break a parser."""
    result = _diff(str(tracked), "--json")

    assert "\N{BOX DRAWINGS LIGHT VERTICAL}" not in result.output
    assert "\x1b[" not in result.output


def test_json_without_an_index_is_rejected_unless_consent_is_given(
    store_root: Path, tracked: Path
) -> None:
    """--json must not build the index silently, prompt, or print to stdout."""
    cache.index_path(store_root).unlink()

    result = runner.invoke(app, ["diff", str(tracked), "--json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "--yes" in result.stderr
    assert not cache.index_exists(store_root)


@pytest.mark.parametrize("flag", ["--yes", "--rebuild"])
def test_json_without_an_index_proceeds_with_consent(
    store_root: Path, tracked: Path, flag: str
) -> None:
    """--yes or --rebuild is consent enough, and stdout stays pure JSON."""
    cache.index_path(store_root).unlink()

    result = runner.invoke(app, ["diff", str(tracked), "--json", flag])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["summary"]["row_delta"] == 6


def test_sensitivity_changes_what_is_reported(store_root: Path, tracked: Path) -> None:
    """The same movement is graded differently at different sensitivities."""
    low = json.loads(_diff(str(tracked), "--json", "--sensitivity", "low").output)
    high = json.loads(_diff(str(tracked), "--json", "--sensitivity", "high").output)

    assert low["options"]["warn_psi"] > high["options"]["warn_psi"]


def test_an_unknown_sensitivity_lists_the_ones_that_exist(
    store_root: Path, tracked: Path
) -> None:
    """A typo should say what to type instead."""
    result = _diff(str(tracked), "--sensitivity", "aggressive")

    assert result.exit_code == 2
    assert "normal" in result.output


# ---------------------------------------------------------------------------
# PDF report
# ---------------------------------------------------------------------------


def test_pdf_writes_the_report_to_the_given_path(
    tmp_path: Path, store_root: Path, tracked: Path
) -> None:
    """`--pdf` leaves a real PDF behind and still exits cleanly."""
    pytest.importorskip("reportlab", reason="the `report` extra is not installed")
    report = tmp_path / "diff.pdf"

    result = _diff(str(tracked), "--pdf", str(report))

    assert result.exit_code == 0, result.output
    assert report.read_bytes().startswith(b"%PDF")


def test_pdf_without_the_report_extra_fails_the_command(
    tmp_path: Path,
    store_root: Path,
    tracked: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A report that was asked for and not written must not look like success."""
    monkeypatch.setitem(sys.modules, "reportlab", None)
    monkeypatch.setitem(sys.modules, "reportlab.lib", None)

    result = _diff(str(tracked), "--pdf", str(tmp_path / "diff.pdf"))

    assert result.exit_code == 1
    assert "dstrack[report]" in result.output


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


def test_diff_outside_a_store_says_how_to_create_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no `.dstrack/` to read, the error points at `dstrack init`."""
    monkeypatch.delenv("DSTRACK_ROOT_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    result = _diff("data.csv")

    assert result.exit_code == 1
    assert "dstrack init" in result.output


def test_an_unknown_target_lists_the_known_datasets(
    tmp_path: Path, store_root: Path, tracked: Path
) -> None:
    """An error a reader can act on names what does exist."""
    result = _diff(str(tmp_path / "nope.csv"))

    assert result.exit_code == 1
    assert "Known datasets" in result.output


def test_a_first_snapshot_has_nothing_to_compare_against(
    tmp_path: Path, store_root: Path
) -> None:
    """One snapshot and no parent is a dead end worth explaining."""
    csv = _write_csv(tmp_path / "only.csv")
    _track(csv)

    result = _diff(str(csv))

    assert result.exit_code == 1
    assert "first snapshot" in result.output


def test_mixing_positionals_with_from_and_to_is_refused(
    store_root: Path, tracked: Path
) -> None:
    """Two spellings of the same thing must not be combined."""
    first, second = _snapshots(store_root, tracked)

    result = runner.invoke(app, ["diff", first, "--to", second, "--yes"])

    assert result.exit_code == 2
    assert "not both" in result.output


def test_more_than_two_references_is_refused(store_root: Path, tracked: Path) -> None:
    """A comparison has exactly two sides."""
    first, second = _snapshots(store_root, tracked)

    result = _diff(first, second, first)

    assert result.exit_code == 2
    assert "at most two" in result.output


def test_no_reference_at_all_is_refused(store_root: Path, tracked: Path) -> None:
    """There is no sensible default for what to compare."""
    result = _diff()

    assert result.exit_code == 2
    assert "at least one" in result.output


def test_no_index_and_no_terminal_says_how_to_proceed(
    tmp_path: Path, store_root: Path
) -> None:
    """A script with no tty cannot be asked, so it is told what to pass."""
    csv = _write_csv(tmp_path / "data.csv")
    _track(csv)

    result = runner.invoke(app, ["diff", str(csv)])

    assert result.exit_code == 1
    assert "--yes" in result.output


# ---------------------------------------------------------------------------
# Cross-dataset
# ---------------------------------------------------------------------------


def test_comparing_two_datasets_warns_but_succeeds(
    tmp_path: Path, store_root: Path
) -> None:
    """Comparing across datasets is supported, and the reader is told."""
    first = _write_csv(tmp_path / "a.csv")
    second = _write_csv(tmp_path / "b.csv", rows=9)
    _track(first)
    _track(second)

    result = _diff(str(first), str(second))

    assert result.exit_code == 0, result.output
    assert "two different datasets" in result.output


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_diff_is_listed_in_the_top_level_help() -> None:
    """A command nobody can discover is a command nobody uses."""
    result = runner.invoke(app, [])

    assert "diff" in result.output
