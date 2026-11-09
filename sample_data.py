"""Generate and mutate a small CSV for trying out `dstrack diff` by hand.

A development helper, not part of the package: it is not tested and is left
out of coverage.

    uv run sample_data.py create     # write data/sample.csv
    dstrack track data/sample.csv
    uv run sample_data.py modify     # apply a handful of changes
    dstrack track data/sample.csv
    dstrack diff data/sample.csv
"""

import csv
import random
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(add_completion=False, no_args_is_help=True)

DATA_DIR = Path("data")
DEFAULT_FILE = "sample.csv"

FIRST_NAMES = ["Ada", "Alan", "Grace", "Katherine", "Linus", "Margaret", "Dennis"]
LAST_NAMES = ["Lovelace", "Turing", "Hopper", "Johnson", "Torvalds", "Hamilton"]
PLANS = ["free", "pro", "enterprise"]
COUNTRIES = ["NL", "DE", "US", "SV", "JP"]

FileOption = Annotated[
    str, typer.Option("--file", "-f", help="File name inside the data/ folder.")
]
SeedOption = Annotated[
    int | None, typer.Option(help="Random seed, for a reproducible result.")
]


def _path(file: str) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / file
    if not path.resolve().is_relative_to(DATA_DIR.resolve()):
        raise typer.BadParameter(f"{file!r} resolves outside {DATA_DIR}/.")
    if path.is_dir():
        raise typer.BadParameter(f"{file!r} is a directory, not a file.")
    return path


def _row(rng: random.Random, row_id: int) -> dict[str, str]:
    return {
        "id": str(row_id),
        "name": f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
        "age": str(rng.randint(18, 75)),
        "balance": f"{rng.uniform(0, 2500):.2f}",
        "signup_date": f"2023-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
        "is_active": rng.choice(["true", "false"]),
        "plan": rng.choice(PLANS),
    }


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def _write(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@app.command()
def create(
    file: FileOption = DEFAULT_FILE,
    rows: Annotated[int, typer.Option(help="Number of rows to generate.")] = 50,
    seed: SeedOption = 0,
) -> None:
    """Write a fresh sample CSV, overwriting any previous one."""
    rng = random.Random(seed)
    data = [_row(rng, i) for i in range(1, rows + 1)]
    path = _path(file)
    _write(path, list(data[0]), data)
    typer.echo(f"Wrote {len(data)} rows to {path}")


@app.command()
def modify(
    file: FileOption = DEFAULT_FILE,
    seed: SeedOption = None,
    schema: Annotated[
        bool, typer.Option(help="Also change the schema (add/drop a column).")
    ] = True,
) -> None:
    """Apply a few simple changes to the sample CSV, in place."""
    path = _path(file)
    if not path.exists():
        typer.echo(f"{path} does not exist; run `create` first.", err=True)
        raise typer.Exit(1)

    rng = random.Random(seed)
    fields, data = _read(path)
    changes: list[str] = []

    # Compute before dropping so new ids never reuse a dropped row's id.
    next_id = max((int(row["id"]) for row in data if row.get("id")), default=0) + 1

    # Drop a few rows.
    dropped = rng.sample(range(len(data)), k=min(3, len(data)))
    data = [row for i, row in enumerate(data) if i not in dropped]
    changes.append(f"dropped {len(dropped)} rows")

    # Append new rows, continuing the id sequence.
    added = rng.randint(3, 8)
    for i in range(added):
        new = _row(rng, next_id + i)
        data.append({field: new.get(field, "") for field in fields})
    changes.append(f"added {added} rows")

    # Shift the balance distribution upwards.
    if "balance" in fields:
        factor = rng.uniform(1.2, 1.8)
        for row in data:
            if row["balance"]:
                row["balance"] = f"{float(row['balance']) * factor:.2f}"
        changes.append(f"scaled balance by {factor:.2f}")

    # Blank out some ages to raise the null rate.
    if "age" in fields:
        nulled = rng.sample(range(len(data)), k=max(1, len(data) // 10))
        for i in nulled:
            data[i]["age"] = ""
        changes.append(f"nulled {len(nulled)} ages")

    # Move part of the user base onto a single plan.
    if "plan" in fields:
        for row in data:
            if rng.random() < 0.3:
                row["plan"] = "enterprise"
        changes.append("moved ~30% of rows to the enterprise plan")

    # Make a column constant and another mostly null, so each joins a set
    # (constant_columns / high_null_columns) in the diff.
    if "is_active" in fields:
        for row in data:
            row["is_active"] = "true"
        changes.append("made is_active constant (all true)")
    if "signup_date" in fields:
        blanked = rng.sample(range(len(data)), k=len(data) * 3 // 4)
        changed = sum(bool(data[i]["signup_date"]) for i in blanked)
        for i in blanked:
            data[i]["signup_date"] = ""
        changes.append(f"nulled {changed} signup dates (>50% null)")

    # Schema: add a country column the first time, drop it the next.
    if schema:
        if "country" in fields:
            fields.remove("country")
            for row in data:
                row.pop("country", None)
            changes.append("dropped column country")
        else:
            fields.append("country")
            for row in data:
                row["country"] = rng.choice(COUNTRIES)
            changes.append("added column country")

    _write(path, fields, data)
    typer.echo(f"Modified {path}:")
    for change in changes:
        typer.echo(f"  - {change}")


if __name__ == "__main__":
    app()
