# dstrack

[![Unit Tests](https://github.com/leoyala/dstrack/actions/workflows/unittests.yaml/badge.svg)](https://github.com/leoyala/dstrack/actions/workflows/unittests.yaml)
[![codecov](https://codecov.io/github/leoyala/dstrack/graph/badge.svg?token=DEKCE4LR88)](https://codecov.io/github/leoyala/dstrack)

<p align="center">
  <img src="docs/assets/dstrack-logo.png" width="200" alt="dstrack logo">
</p>


A Python package for versioning and monitoring dataset changes throughout the machine learning lifecycle.

## Overview

`dstrack` helps data scientists and ML engineers track how datasets evolve over time, catching schema drift, distribution shifts, and unexpected mutations before they silently break pipelines or degrade model performance.

## Installation

```bash
pip install dstrack
```

Requires Python 3.11 or later.

## Getting started

Initialize a store in your project, then take your first snapshot.

**1. Create a local store** - this adds a `.dstrack/` directory at the current location:

```bash
dstrack init
```

```text
ℹ Generating local store structure at /path/to/.dstrack.
✔ Finished creating local store: /path/to/.dstrack
```

**2. Track a dataset** - point `dstrack` at a data file to snapshot it. Given a small
`data.csv`:

```csv
id,name,value
1,alpha,10.5
2,beta,20.0
3,gamma,15.25
```

```bash
dstrack track data.csv
```

```text
ℹ Reading data.csv and computing snapshot...
✔ Snapshot <snapshot-uuid> written (new dataset, dataset <dataset-uuid>).
ℹ Stored at /path/to/.dstrack/datasets/<dataset-uuid>/snapshots/<snapshot-uuid>.json
```

**3. Re-track as the data changes** - running `track` again on the same path extends the
dataset's lineage instead of starting a new one:

```bash
dstrack track data.csv
```

```text
✔ Snapshot <snapshot-uuid> written (continued lineage, dataset <dataset-uuid>).
```

Each snapshot captures the file's schema, a content fingerprint, and per-column
statistics. See the [Getting Started guide](https://leoyala.github.io/dstrack/getting_started/)
for options such as `--name`, `--reader`, and `--dataset-id`.

## Features

- **Dataset versioning** - snapshot a dataset and track its lineage across pipeline stages
- **Rich snapshots** - schema hash, content fingerprint, and per-column statistics (ranges, null rates, and more)
- **CSV out of the box** - pure standard-library reader, no heavy dependencies
- **Lightweight CLI** - a small, git-like local store you can commit alongside your code

## Roadmap

Change detection and drift monitoring are on the way - comparing snapshots, surfacing
schema and distribution shifts, and failing CI on drift. See the
[roadmap](docs/roadmap.md) for what's planned.
