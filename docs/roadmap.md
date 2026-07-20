---
icon: lucide/route
---

# Roadmap

This page tracks the planned development of `dstrack`. Each milestone ships as a versioned release and builds directly on the previous one.

## v0.1 - Project Skeleton

Foundation tooling: packaging, linting, type checking, CI/CD, and release automation.

- [x] Package structure (`src/` layout, `pyproject.toml`)
- [x] Linting and formatting (`ruff`)
- [x] Static type checking (`mypy` strict mode)
- [x] Pre-commit hooks (formatting, secret scanning, notebook output stripping)
- [x] Tag-triggered PyPI release workflow
- [x] Smoke test suite against built distributions

Foundation for all future capabilities: reading a dataset and capturing an immutable record of its state.

- [x] Local snapshot store
- [x] CSV support (no extra dependencies)
- [x] Content fingerprinting (deterministic, format-agnostic)
- [x] Schema extraction (column names and types)
- [x] Per-column statistics (counts, ranges, null rates, etc.)
- [x] `dstrack init` and `dstrack track` commands
- [x] `dstrack log` command

## v0.2 - Diff Engine

Compare two snapshots and surface what changed between them.

- [ ] Schema diff (added, removed, and type-changed columns)
- [ ] Statistics diff (value range and distribution shifts)
- [ ] Basic drift indicators
- [ ] `dstrack diff` command
- [ ] Structured JSON output for machine consumption

## v0.3 - CLI Polish

A first-class command-line experience ready for daily use.

- [ ] `dstrack status` command (compare current dataset to latest snapshot)
- [ ] Rich terminal output with color-coded severity
- [ ] `--fail-on-drift` exit code for CI pipelines
- [ ] `--json` flag on all commands
- [ ] Shell completion

## v0.4 - Format and Integration Expansion

Support the data formats researchers and engineers actually use.

- [ ] Parquet support (optional install extra)
- [ ] JSON Lines support
- [ ] NumPy array support (optional install extra)
- [ ] `dstrack.pandas` convenience module for snapshotting DataFrames directly

## v0.5 - CI/CD Integration

Make drift detection a native step in automated pipelines.

- [ ] GitHub Actions composite action (`dstrack-action`)
- [ ] Integration into other frameworks: polars, moflow, etc.
- [ ] CI integration documentation and working examples

## v0.6 - Migration Engine & Documentation and Examples

Everything a new user needs to be productive in under 10 minutes.

- [ ] Database migration engine
- [ ] Quickstart guide (end-to-end in 5 minutes)
- [ ] Concept guide (snapshots, drift, stores)
- [ ] How-to guides (CI integration, remote storage, custom detectors)
- [ ] Example notebooks (ML pipeline walkthrough, drift detection demo)
- [ ] API reference (auto-generated from source)
- [ ] Automated docs deployment on each release

## v1.0 - Stable Release

A production-ready release with documented compatibility guarantees.

- [ ] Stable public API with semantic versioning commitment
- [ ] Snapshot store migration tooling
- [ ] Full test matrix across all supported Python versions
- [ ] Performance benchmarks for large datasets
- [ ] Security guidance (PII detection warnings, audit trail)
