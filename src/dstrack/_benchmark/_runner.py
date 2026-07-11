"""Runs the snapshot pipeline against a synthetic CSV and times each phase.

The runner performs no output of its own: progress is announced through a
:class:`BenchmarkObserver` and everything measured is returned as a
:class:`BenchmarkRun`, so the same run can be driven from a test with no
console attached.
"""

import cProfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dstrack._benchmark._environment import EnvironmentInfo, collect_environment_info
from dstrack._benchmark._profiling import CallGraph, dstrack_scope
from dstrack._benchmark._synthetic import SyntheticCsvSpec, write_synthetic_csv
from dstrack.readers import CsvReader
from dstrack.snapshot import MetadataBuilder, StatsComputer

_DATASET_NAME = "benchmark-dataset"
_CREATED_BY = "dstrack-benchmark"


@dataclass(frozen=True)
class BenchmarkResult:
    """Timings and sizing for one synthetic-CSV snapshot benchmark run.

    Attributes:
        num_rows: Rows written to the synthetic CSV.
        num_rows_read: Rows the statistics pass actually read back.
        num_columns: Columns the reader inferred, including ``id``.
        csv_size_bytes: Size of the generated CSV on disk.
        generation_seconds: Time to write the synthetic CSV.
        metadata_seconds: Time to build snapshot metadata from the reader.
        stats_seconds: Time for the statistics data pass.
        schema_hash: Schema hash of the snapshot that was built.
    """

    num_rows: int
    num_rows_read: int
    num_columns: int
    csv_size_bytes: int
    generation_seconds: float
    metadata_seconds: float
    stats_seconds: float
    schema_hash: str

    @property
    def total_snapshot_seconds(self) -> float:
        """Time to build the snapshot: metadata plus statistics."""
        return self.metadata_seconds + self.stats_seconds

    @property
    def rows_per_second(self) -> float:
        """Rows processed per second of snapshot time."""
        return self.num_rows_read / self.total_snapshot_seconds


@dataclass(frozen=True)
class BenchmarkRun:
    """Everything one benchmark run produced, ready to be reported."""

    result: BenchmarkResult
    environment: EnvironmentInfo
    call_graph: CallGraph | None


class BenchmarkObserver(Protocol):
    """Receives a benchmark's progress as each phase starts."""

    def generating_csv(self, path: Path, num_rows: int) -> None:
        """Called before the synthetic CSV is written."""
        ...

    def building_metadata(self) -> None:
        """Called before snapshot metadata is built."""
        ...

    def computing_stats(self) -> None:
        """Called before the statistics data pass."""
        ...


class SilentObserver:
    """Observer that reports nothing; the default for programmatic runs."""

    def generating_csv(self, path: Path, num_rows: int) -> None:
        return None

    def building_metadata(self) -> None:
        return None

    def computing_stats(self) -> None:
        return None


@dataclass
class _Elapsed:
    """Wall-clock seconds a phase took, filled in when its block exits."""

    seconds: float = 0.0


class BenchmarkRunner:
    """Generates a synthetic CSV and times a snapshot being built from it.

    Metadata and statistics are timed separately, and share one profiler so the
    call tree covers the whole snapshot pipeline.  CSV generation is never
    profiled: it measures the harness, not dstrack.

    Args:
        spec: The synthetic dataset to benchmark against.
        observer: Notified as each phase starts.  Defaults to reporting nothing.
        profile: Whether to profile dstrack's own methods while building the
            snapshot.  Adds cProfile overhead to the reported timings.
    """

    def __init__(
        self,
        spec: SyntheticCsvSpec,
        *,
        observer: BenchmarkObserver | None = None,
        profile: bool = True,
    ) -> None:
        self._spec = spec
        self._observer = observer or SilentObserver()
        self._profiler = cProfile.Profile() if profile else None

    def run(self, csv_path: Path) -> BenchmarkRun:
        """Generate the dataset at ``csv_path`` and benchmark a snapshot of it."""
        self._observer.generating_csv(csv_path, self._spec.num_rows)
        with self._timed() as generation:
            write_synthetic_csv(csv_path, self._spec)
        csv_size_bytes = csv_path.stat().st_size

        reader = CsvReader(csv_path)

        self._observer.building_metadata()
        with self._timed(profiled=True) as metadata_phase:
            metadata = MetadataBuilder().build(
                reader,
                dataset_name=_DATASET_NAME,
                dataset_path=csv_path,
                source_type="file",
                created_by=_CREATED_BY,
            )

        self._observer.computing_stats()
        with self._timed(profiled=True) as stats_phase:
            stats = StatsComputer().compute(reader)

        result = BenchmarkResult(
            num_rows=self._spec.num_rows,
            num_rows_read=stats.num_rows,
            num_columns=metadata.num_columns,
            csv_size_bytes=csv_size_bytes,
            generation_seconds=generation.seconds,
            metadata_seconds=metadata_phase.seconds,
            stats_seconds=stats_phase.seconds,
            schema_hash=metadata.schema_hash,
        )
        return BenchmarkRun(
            result=result,
            environment=collect_environment_info(),
            call_graph=self._call_graph(),
        )

    @contextmanager
    def _timed(self, *, profiled: bool = False) -> Iterator[_Elapsed]:
        """Time the block, optionally recording it into the shared profiler."""
        profiler = self._profiler if profiled else None
        elapsed = _Elapsed()
        if profiler is not None:
            profiler.enable()
        start = time.perf_counter()
        try:
            yield elapsed
        finally:
            elapsed.seconds = time.perf_counter() - start
            if profiler is not None:
                profiler.disable()

    def _call_graph(self) -> CallGraph | None:
        """Get a call graph of the snapshot phases, or None if profiling was disabled."""
        if self._profiler is None:
            return None
        return CallGraph.from_profile(self._profiler, dstrack_scope())
