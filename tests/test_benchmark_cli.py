"""Tests for the benchmark CLI in src/dstrack/_benchmark/_cli.py."""

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pytest
from typer.testing import CliRunner

from dstrack._benchmark import _cli
from dstrack._benchmark._cli import _workspace, app
from dstrack._benchmark._environment import collect_environment_info
from dstrack._benchmark._runner import BenchmarkResult, BenchmarkRun
from dstrack._benchmark._synthetic import SyntheticCsvSpec

runner = CliRunner()

# `run` is a named subcommand: an explicit app callback keeps the command group
# intact, so a bare `dstrack-benchmark` shows help instead of benchmarking.


@dataclass
class _RecordingReporter:
    """Stands in for ConsoleReporter, recording what the CLI asked it to print."""

    kept_paths: list[Path] = field(default_factory=list)
    reported: list[tuple[BenchmarkRun, int]] = field(default_factory=list)

    def report(self, run: BenchmarkRun, *, profile_limit: int) -> None:
        self.reported.append((run, profile_limit))

    def csv_kept(self, path: Path) -> None:
        self.kept_paths.append(path)


def _make_run() -> BenchmarkRun:
    """A canned run, so stubbed runners need not do any real work."""
    result = BenchmarkResult(
        num_rows=10,
        num_rows_read=10,
        num_columns=3,
        csv_size_bytes=128,
        generation_seconds=0.1,
        metadata_seconds=0.2,
        stats_seconds=0.3,
        schema_hash="deadbeef",
    )
    return BenchmarkRun(
        result=result, environment=collect_environment_info(), call_graph=None
    )


class _StubRunner:
    """Stands in for BenchmarkRunner, recording how the CLI constructed it."""

    instances: ClassVar[list["_StubRunner"]] = []

    def __init__(
        self, spec: SyntheticCsvSpec, *, observer: object = None, profile: bool = True
    ) -> None:
        self.spec = spec
        self.observer = observer
        self.profile = profile
        self.csv_paths: list[Path] = []
        type(self).instances.append(self)

    def run(self, csv_path: Path) -> BenchmarkRun:
        csv_path.write_text("id\n0\n", encoding="utf-8")
        self.csv_paths.append(csv_path)
        return _make_run()


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch) -> _RecordingReporter:
    """Swap the runner and reporter out, so the CLI's wiring is tested alone."""
    _StubRunner.instances = []
    reporter = _RecordingReporter()
    monkeypatch.setattr(_cli, "BenchmarkRunner", _StubRunner)
    monkeypatch.setattr(_cli, "ConsoleReporter", lambda: reporter)
    return reporter


# ---------------------------------------------------------------------------
# _workspace
# ---------------------------------------------------------------------------


def test_workspace_yields_csv_path_in_a_fresh_directory() -> None:
    """Yields `synthetic_dataset.csv` inside a directory that exists but is empty."""
    reporter = _RecordingReporter()

    with _workspace(reporter, keep_csv=False) as csv_path:
        assert csv_path.name == "synthetic_dataset.csv"
        assert csv_path.parent.is_dir()
        assert not csv_path.exists()
        assert list(csv_path.parent.iterdir()) == []


def test_workspace_discards_the_directory_by_default() -> None:
    """Removes the temp directory, and the CSV in it, once the block exits."""
    reporter = _RecordingReporter()

    with _workspace(reporter, keep_csv=False) as csv_path:
        csv_path.write_text("id\n0\n", encoding="utf-8")
        tmp_dir = csv_path.parent

    assert not csv_path.exists()
    assert not tmp_dir.exists()
    assert reporter.kept_paths == []


def test_workspace_keeps_the_csv_when_asked() -> None:
    """With keep_csv, leaves the CSV on disk and reports where it went."""
    reporter = _RecordingReporter()

    with _workspace(reporter, keep_csv=True) as csv_path:
        csv_path.write_text("id\n0\n", encoding="utf-8")

    assert csv_path.is_file()
    assert csv_path.read_text(encoding="utf-8") == "id\n0\n"
    assert reporter.kept_paths == [csv_path]

    # keep_csv deliberately leaves the directory behind, so clean it up here.
    shutil.rmtree(csv_path.parent, ignore_errors=True)


def test_workspace_cleans_up_when_the_block_raises() -> None:
    """Propagates the error, but still discards the temp directory."""
    reporter = _RecordingReporter()
    leaked: list[Path] = []

    with (
        pytest.raises(RuntimeError, match="boom"),
        _workspace(reporter, keep_csv=False) as csv_path,
    ):
        csv_path.write_text("id\n0\n", encoding="utf-8")
        leaked.append(csv_path.parent)
        raise RuntimeError("boom")

    assert not leaked[0].exists()


def test_workspace_uses_a_distinct_directory_per_run() -> None:
    """Concurrent or repeated runs never collide on the same temp directory."""
    reporter = _RecordingReporter()

    with (
        _workspace(reporter, keep_csv=False) as first,
        _workspace(reporter, keep_csv=False) as second,
    ):
        assert first.parent != second.parent


# ---------------------------------------------------------------------------
# `run` command: option wiring
# ---------------------------------------------------------------------------


def test_run_defaults_match_the_synthetic_spec_defaults(
    stubbed: _RecordingReporter,
) -> None:
    """With no options, the spec the runner gets is an untouched SyntheticCsvSpec."""
    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0
    assert _StubRunner.instances[0].spec == SyntheticCsvSpec()


def test_run_forwards_every_option_to_the_spec(stubbed: _RecordingReporter) -> None:
    """Each dataset-shaping option lands on the matching spec field."""
    result = runner.invoke(
        app,
        [
            "run",
            "--rows", "1234",
            "--numeric-cols", "7",
            "--string-cols", "6",
            "--datetime-cols", "5",
            "--bool-cols", "4",
            "--null-rate", "0.25",
            "--seed", "99",
        ],
    )  # fmt: skip

    assert result.exit_code == 0
    assert _StubRunner.instances[0].spec == SyntheticCsvSpec(
        num_rows=1234,
        num_numeric_cols=7,
        num_string_cols=6,
        num_datetime_cols=5,
        num_bool_cols=4,
        null_rate=0.25,
        seed=99,
    )


def test_run_uses_the_reporter_as_the_runners_observer(
    stubbed: _RecordingReporter,
) -> None:
    """The reporter that prints the tables also receives the progress callbacks."""
    result = runner.invoke(app, ["run", "--rows", "10"])

    assert result.exit_code == 0
    assert _StubRunner.instances[0].observer is stubbed


def test_run_profiles_by_default(stubbed: _RecordingReporter) -> None:
    """Profiling is on unless it is explicitly turned off."""
    result = runner.invoke(app, ["run", "--rows", "10"])

    assert result.exit_code == 0
    assert _StubRunner.instances[0].profile is True


def test_run_no_profile_disables_profiling(stubbed: _RecordingReporter) -> None:
    """--no-profile reaches the runner, so no profiler is attached."""
    result = runner.invoke(app, ["run", "--rows", "10", "--no-profile"])

    assert result.exit_code == 0
    assert _StubRunner.instances[0].profile is False


def test_run_forwards_the_profile_limit_to_the_report(
    stubbed: _RecordingReporter,
) -> None:
    """--profile-limit is a reporting concern, and reaches report()."""
    result = runner.invoke(app, ["run", "--rows", "10", "--profile-limit", "3"])

    assert result.exit_code == 0
    assert [limit for _, limit in stubbed.reported] == [3]


def test_run_defaults_the_profile_limit_to_20(stubbed: _RecordingReporter) -> None:
    """The default call-tree width is 20 children per node."""
    result = runner.invoke(app, ["run", "--rows", "10"])

    assert result.exit_code == 0
    assert [limit for _, limit in stubbed.reported] == [20]


def test_run_reports_the_run_the_runner_produced(stubbed: _RecordingReporter) -> None:
    """Whatever the runner returns is handed straight to the reporter."""
    result = runner.invoke(app, ["run", "--rows", "10"])

    assert result.exit_code == 0
    assert len(stubbed.reported) == 1
    reported_run, _ = stubbed.reported[0]
    assert reported_run.result.schema_hash == "deadbeef"


def test_run_benchmarks_the_csv_inside_the_workspace(
    stubbed: _RecordingReporter,
) -> None:
    """The path handed to the runner is the workspace CSV, and it is cleaned up."""
    result = runner.invoke(app, ["run", "--rows", "10"])

    assert result.exit_code == 0
    csv_path = _StubRunner.instances[0].csv_paths[0]
    assert csv_path.name == "synthetic_dataset.csv"
    assert not csv_path.parent.exists()
    assert stubbed.kept_paths == []


def test_run_keep_csv_leaves_the_generated_csv_behind(
    stubbed: _RecordingReporter,
) -> None:
    """With --keep-csv the file survives the run and is reported to the user."""
    result = runner.invoke(app, ["run", "--rows", "10", "--keep-csv"])

    assert result.exit_code == 0
    csv_path = _StubRunner.instances[0].csv_paths[0]
    assert csv_path.is_file()
    assert stubbed.kept_paths == [csv_path]

    shutil.rmtree(csv_path.parent, ignore_errors=True)


def test_run_rejects_a_non_numeric_option(stubbed: _RecordingReporter) -> None:
    """Bad option types fail as a usage error, without constructing a runner."""
    result = runner.invoke(app, ["run", "--rows", "many"])

    assert result.exit_code != 0
    assert _StubRunner.instances == []


# ---------------------------------------------------------------------------
# `run` command: end to end
# ---------------------------------------------------------------------------


def test_run_end_to_end_prints_all_three_tables() -> None:
    """A real (tiny) run generates a CSV, snapshots it, and prints every table."""
    result = runner.invoke(app, ["run", "--rows", "50", "--profile-limit", "5"])

    assert result.exit_code == 0
    output = result.output
    assert "Generating synthetic CSV" in output
    assert "Building snapshot metadata" in output
    assert "Computing snapshot statistics" in output
    assert "Environment" in output
    assert "Snapshot benchmark" in output
    assert "call tree" in output


def test_run_end_to_end_without_profiling_omits_the_call_tree() -> None:
    """--no-profile still reports timings, but there is no call tree to show."""
    result = runner.invoke(app, ["run", "--rows", "50", "--no-profile"])

    assert result.exit_code == 0
    assert "Snapshot benchmark" in result.output
    assert "call tree" not in result.output


def test_run_end_to_end_keep_csv_writes_a_readable_dataset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The kept CSV is the dataset that was benchmarked: header plus the rows asked for."""
    monkeypatch.setattr(_cli.tempfile, "mkdtemp", lambda prefix: str(tmp_path))

    result = runner.invoke(
        app, ["run", "--rows", "50", "--string-cols", "1", "--no-profile", "--keep-csv"]
    )

    assert result.exit_code == 0
    csv_path = tmp_path / "synthetic_dataset.csv"
    assert csv_path.is_file()
    assert "Synthetic CSV kept at" in result.output

    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("id,")
    assert "string_0" in lines[0]
    assert len(lines) == 51  # header + 50 rows


def test_run_end_to_end_is_reproducible_for_a_fixed_seed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The same seed benchmarks the same bytes, so runs are comparable."""
    contents: list[str] = []
    for i in range(2):
        out_dir = tmp_path / str(i)
        out_dir.mkdir()
        monkeypatch.setattr(_cli.tempfile, "mkdtemp", lambda prefix, d=out_dir: str(d))

        result = runner.invoke(
            app, ["run", "--rows", "50", "--seed", "7", "--no-profile", "--keep-csv"]
        )

        assert result.exit_code == 0
        contents.append((out_dir / "synthetic_dataset.csv").read_text(encoding="utf-8"))

    assert contents[0] == contents[1]


def test_run_surfaces_a_runner_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure mid-run exits non-zero rather than reporting a bogus benchmark."""

    class _ExplodingRunner:
        def __init__(self, spec: SyntheticCsvSpec, **kwargs: object) -> None:
            self.leaked: list[Path] = []

        def run(self, csv_path: Path) -> BenchmarkRun:
            leaked.append(csv_path.parent)
            raise RuntimeError("reader exploded")

    leaked: list[Path] = []
    monkeypatch.setattr(_cli, "BenchmarkRunner", _ExplodingRunner)

    result = runner.invoke(app, ["run", "--rows", "10"])

    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)
    assert not leaked[0].exists()  # the workspace is still cleaned up


def test_no_args_shows_help_without_running_a_benchmark() -> None:
    """A bare invocation prints help and lists `run`, never starting a benchmark."""
    result = runner.invoke(app, [])

    assert result.exit_code != 0
    assert "Usage" in result.output
    assert "run" in result.output
    assert "Snapshot benchmark" not in result.output
