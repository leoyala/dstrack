"""Benchmark for dstrack's snapshot creation pipeline.

Generates a synthetic CSV in a temporary directory, then times how long it
takes to build snapshot metadata and statistics for it, alongside a summary of
the environment the benchmark ran in and a call tree of dstrack's own methods.

Layers
------
:mod:`._synthetic`
    Describes and writes the synthetic dataset.

:mod:`._runner`
    Runs the snapshot pipeline against it and times each phase.

:mod:`._profiling`
    Reduces the ``cProfile`` run to a call tree of dstrack's own methods.

:mod:`._environment`
    Describes the machine the benchmark ran on.

:mod:`._report`, :mod:`._cli`
    Render runs to a console, and wire the whole thing to ``dstrack-benchmark``.
"""

from ._cli import app
from ._environment import EnvironmentInfo, collect_environment_info
from ._profiling import CallGraph, CallNode, CallScope
from ._report import ConsoleReporter
from ._runner import (
    BenchmarkObserver,
    BenchmarkResult,
    BenchmarkRun,
    BenchmarkRunner,
    SilentObserver,
)
from ._synthetic import SyntheticColumn, SyntheticCsvSpec, write_synthetic_csv

__all__ = [
    "BenchmarkObserver",
    "BenchmarkResult",
    "BenchmarkRun",
    "BenchmarkRunner",
    "CallGraph",
    "CallNode",
    "CallScope",
    "ConsoleReporter",
    "EnvironmentInfo",
    "SilentObserver",
    "SyntheticColumn",
    "SyntheticCsvSpec",
    "app",
    "collect_environment_info",
    "write_synthetic_csv",
]
