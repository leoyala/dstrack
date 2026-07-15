---
icon: lucide/play
---

# Getting Started

This guide walks through the full `dstrack` workflow: initializing a store, taking your
first snapshot, and understanding how snapshots and lineage work.

## Prerequisites

- **Python 3.11 or later**
- Install the package:

```bash
pip install dstrack
```

- Verify the installation:

```bash
dstrack version
# 0.1.0
```

## 1. Initialize a store

`dstrack` keeps its history in a local store - a `.dstrack/` directory, conceptually
similar to git's `.git/`. Create one at the root of your project:

```bash
dstrack init
```

```text
ℹ Generating local store structure at /path/to/.dstrack.
✔ Finished creating local store: /path/to/.dstrack
```

This creates:

```text
.dstrack/
├── datasets/     # one directory per tracked dataset
└── .gitignore    # ignores the local .cache/ directory
```

A few things worth knowing:

- The store is discovered by walking **up** the directory tree from where you run a
  command, so you can `track` datasets from any subdirectory of your project.
- Set the `DSTRACK_ROOT_PATH` environment variable to point at a store elsewhere.
- Re-running `init` where a store already exists fails by design. Pass `--allow-exists`
  to turn that into a warning instead:

```bash
dstrack init --allow-exists
```

```text
⚡ Local store path already exists: /path/to/.dstrack
```

## 2. Track a dataset

Tracking reads a data file, computes a **snapshot**, and stores it. Create a small
`data.csv` to follow along:

```csv
id,name,value
1,alpha,10.5
2,beta,20.0
3,gamma,15.25
```

Then snapshot it:

```bash
dstrack track data.csv
```

```text
ℹ Reading data.csv and computing snapshot...
✔ Snapshot <snapshot-uuid> written (new dataset, dataset <dataset-uuid>).
ℹ Stored at /path/to/.dstrack/datasets/<dataset-uuid>/snapshots/<snapshot-uuid>.json
```

A snapshot is an immutable record of the dataset's state at that moment. It captures:

- **Schema** - column names and inferred types, plus an order-independent `schema_hash`
- **Content fingerprint** - a SHA-256 hash of the source file
- **Per-column statistics** - counts, ranges, null rates, and distribution summaries

The dataset name defaults to the file stem (`data` above); override it with `--name`.

## 3. Snapshots and lineage

Run `track` again on the **same path** and `dstrack` recognizes the dataset, extending its
lineage rather than starting a new one. The new snapshot points back to the previous one
via its `parent_snapshot_id`:

```bash
dstrack track data.csv
```

```text
✔ Snapshot <snapshot-uuid> written (continued lineage, dataset <dataset-uuid>).
```

If you rename or move a dataset file, the recorded path no longer matches, so `dstrack`
would start a new lineage. To keep the history connected, continue the existing dataset
explicitly with `--dataset-id`:

```bash
dstrack track renamed.csv --dataset-id <dataset-uuid>
```
For more information on what `dstrack track` allows you to do, simply ask for the help:

```bash
dstrack track --help
```

## Where snapshots live

Each snapshot is a JSON file under its dataset's directory:

```text
.dstrack/
└── datasets/
    └── <dataset-uuid>/
        └── snapshots/
            └── <snapshot-uuid>.json
```

The store also keeps an append-only log and a `HEAD` pointer per dataset. `.dstrack/` is
plain text and safe to commit alongside your code, giving you a versioned audit trail of
how your datasets evolved.

## Benchmarking (optional)

`dstrack` ships a second command, `dstrack-benchmark`, that generates a synthetic CSV and
measures snapshot-creation performance - handy for understanding overhead on large files:

```bash
dstrack-benchmark run --rows 100000
```

This command creates a synthetic file in a temporary location to measure performance.

## What's next

Change detection and drift monitoring - comparing snapshots to surface schema and
distribution shifts, and failing CI when data drifts - are planned. See the
[roadmap](roadmap.md) for the full picture.
