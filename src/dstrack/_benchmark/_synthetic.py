"""Synthetic CSV generation for the benchmark.

The dataset is described by a
[SyntheticCsvSpec][dstrack._benchmark._synthetic.SyntheticCsvSpec] value object
and written by
[write_synthetic_csv()][dstrack._benchmark._synthetic.write_synthetic_csv].  Each
generated dtype is one
[SyntheticColumn][dstrack._benchmark._synthetic.SyntheticColumn] whose
[generate][dstrack._benchmark._synthetic.SyntheticColumn.generate] callable
renders a single cell, so supporting a new dtype means adding one cell factory
and one entry to ``_COLUMN_KINDS`` rather than threading another count through
every layer.
"""

import csv
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeAlias

# Renders one cell as the text that lands in the CSV.  Cells are always
# generated from the spec's seeded RNG, so a spec plus a seed is reproducible.
CellFactory: TypeAlias = Callable[[random.Random], str]

_WORD_POOL: tuple[str, ...] = (
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
    "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa",
    "quebec", "romeo", "sierra", "tango", "uniform", "victor", "whiskey",
)  # fmt: skip

_DATETIME_START = datetime(2020, 1, 1, tzinfo=UTC)
_DATETIME_END = datetime(2026, 1, 1, tzinfo=UTC)


def _numeric_cell(rng: random.Random) -> str:
    return f"{rng.gauss(100, 25):.4f}"


def _string_cell(rng: random.Random) -> str:
    return " ".join(rng.choices(_WORD_POOL, k=rng.randint(1, 4)))


def _datetime_cell(rng: random.Random) -> str:
    span = (_DATETIME_END - _DATETIME_START).total_seconds()
    return (_DATETIME_START + timedelta(seconds=rng.uniform(0, span))).isoformat()


def _bool_cell(rng: random.Random) -> str:
    return rng.choice(["True", "False"])


@dataclass(frozen=True)
class SyntheticColumn:
    """One generated column: its header name and how to render a cell.

    Attributes:
        name: Column header written to the CSV.
        generate: Renders a single non-null cell from the seeded RNG.
    """

    name: str
    generate: CellFactory


# (name prefix, cell factory) per dtype dstrack's CSV reader infers, in the
# order the columns appear in the file.
_COLUMN_KINDS: tuple[tuple[str, CellFactory], ...] = (
    ("numeric", _numeric_cell),
    ("string", _string_cell),
    ("datetime", _datetime_cell),
    ("bool", _bool_cell),
)


@dataclass(frozen=True)
class SyntheticCsvSpec:
    """Describes the synthetic dataset a benchmark run should be measured on.

    Attributes:
        num_rows: Number of data rows to generate.
        num_numeric_cols: Number of ``float64`` columns.
        num_string_cols: Number of ``string`` columns.
        num_datetime_cols: Number of ``datetime64`` columns.
        num_bool_cols: Number of ``bool`` columns.
        null_rate: Fraction of non-id cells left empty (null).
        seed: Seed for the random generator, for reproducible datasets.
    """

    num_rows: int = 200_000
    num_numeric_cols: int = 5
    num_string_cols: int = 5
    num_datetime_cols: int = 2
    num_bool_cols: int = 2
    null_rate: float = 0.02
    seed: int = 42

    def columns(self) -> list[SyntheticColumn]:
        """Return the nullable, randomly generated columns, in file order.

        The leading ``id`` column is not included: it is written from the row
        index and is never null.
        """
        counts = (
            self.num_numeric_cols,
            self.num_string_cols,
            self.num_datetime_cols,
            self.num_bool_cols,
        )
        return [
            SyntheticColumn(f"{prefix}_{i}", generate)
            for (prefix, generate), count in zip(_COLUMN_KINDS, counts, strict=True)
            for i in range(count)
        ]


def write_synthetic_csv(path: Path, spec: SyntheticCsvSpec) -> None:
    """Write the dataset described by ``spec`` to ``path``, overwriting it.

    Args:
        path: Destination CSV path; overwritten if it already exists.
        spec: The dataset to generate.
    """
    rng = random.Random(spec.seed)
    columns = spec.columns()

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", *(column.name for column in columns)])
        for row_id in range(spec.num_rows):
            row = [str(row_id)]
            row.extend(
                "" if rng.random() < spec.null_rate else column.generate(rng)
                for column in columns
            )
            writer.writerow(row)
