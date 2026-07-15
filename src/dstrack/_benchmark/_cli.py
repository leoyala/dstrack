"""Command-line entry point for the snapshot benchmark."""

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from dstrack._benchmark._report import ConsoleReporter
from dstrack._benchmark._runner import BenchmarkRunner
from dstrack._benchmark._synthetic import SyntheticCsvSpec

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="Benchmark dstrack's snapshot creation pipeline against a synthetic CSV.",
)

_DEFAULTS = SyntheticCsvSpec()


@app.callback()
def _main() -> None:
    """Keep ``run`` a named subcommand so a bare invocation shows help.

    Typer promotes a lone command to the top level, which quietly discards
    ``no_args_is_help``; an explicit callback keeps the command group intact so
    running ``dstrack-benchmark`` with no arguments prints help instead of
    silently kicking off a full-size benchmark.
    """


@contextmanager
def _workspace(reporter: ConsoleReporter, *, keep_csv: bool) -> Iterator[Path]:
    """Yield a path for the synthetic CSV, discarding it unless ``keep_csv``."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="dstrack-benchmark-"))
    csv_path = tmp_dir / "synthetic_dataset.csv"
    try:
        yield csv_path
    finally:
        if keep_csv:
            reporter.csv_kept(csv_path)
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@app.command(help="Generate a synthetic CSV and benchmark snapshot creation on it.")
def run(
    rows: Annotated[
        int, typer.Option(min=0, help="Number of data rows to generate.")
    ] = _DEFAULTS.num_rows,
    numeric_cols: Annotated[
        int, typer.Option(min=0, help="Number of numeric (float) columns.")
    ] = _DEFAULTS.num_numeric_cols,
    string_cols: Annotated[
        int, typer.Option(min=0, help="Number of string columns.")
    ] = _DEFAULTS.num_string_cols,
    datetime_cols: Annotated[
        int, typer.Option(min=0, help="Number of datetime columns.")
    ] = _DEFAULTS.num_datetime_cols,
    bool_cols: Annotated[
        int, typer.Option(min=0, help="Number of boolean columns.")
    ] = _DEFAULTS.num_bool_cols,
    null_rate: Annotated[
        float,
        typer.Option(min=0.0, max=1.0, help="Fraction of non-id cells left null."),
    ] = _DEFAULTS.null_rate,
    seed: Annotated[
        int, typer.Option(help="Random seed for reproducible synthetic data.")
    ] = _DEFAULTS.seed,
    keep_csv: Annotated[
        bool,
        typer.Option(
            help="Keep the generated CSV instead of deleting it after the run."
        ),
    ] = False,
    profile: Annotated[
        bool,
        typer.Option(
            help="Profile dstrack's own methods while building the snapshot. "
            "Adds cProfile overhead to the reported metadata/stats timings."
        ),
    ] = True,
    profile_limit: Annotated[
        int,
        typer.Option(
            min=0,
            help="Max number of child methods shown under each node of the "
            "profile call tree, ranked by cumulative time.",
        ),
    ] = 20,
) -> None:
    spec = SyntheticCsvSpec(
        num_rows=rows,
        num_numeric_cols=numeric_cols,
        num_string_cols=string_cols,
        num_datetime_cols=datetime_cols,
        num_bool_cols=bool_cols,
        null_rate=null_rate,
        seed=seed,
    )
    reporter = ConsoleReporter()
    runner = BenchmarkRunner(spec, observer=reporter, profile=profile)

    with _workspace(reporter, keep_csv=keep_csv) as csv_path:
        benchmark_run = runner.run(csv_path)
        reporter.report(benchmark_run, profile_limit=profile_limit)


if __name__ == "__main__":
    app()
