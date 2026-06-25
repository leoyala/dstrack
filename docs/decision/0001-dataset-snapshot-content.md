---
icon: lucide/puzzle
---

# ADR-0001: Snapshots contain semantic information

## Status
:lucide-circle-check: Accepted

## Context
The main purpose of this package is to track potential semantic changes
on datasets, for example the fact that new classes have been
added or removed, that distributions have changed, etc.
There are other tools that either support snapshotting the entire dataset (`DVC, git LFS, etc.`),
or store dataset versions within their own platform ecosystem (`W&B, HuggingFace, Valohai, etc.`).

## Decision
Snapshots should contain the following semantic dataset information in each snapshot, grouped by concern.

### Snapshot Identity & Provenance
| Field | Description |
|---|---|
| `format_version` | Schema version; required for forward compatibility |
| `snapshot_id` | Unique identifier (e.g. UUID) for this snapshot |
| `created_at` | ISO-8601 timestamp of when the snapshot was taken |
| `created_by` | User or process that created it |
| `dataset_name` | Human-readable name for the dataset |
| `dataset_path` | Source path or URI at snapshot time |
| `source_type` | Origin kind: `file`, `directory`, `database`, `huggingface`, `s3`, etc. |
| `source_hash` | Cryptographic hash of the raw source bytes or Merkle tree hash of a directory |
| `description` | Free-text description of what this dataset represents |
| `tags` | Arbitrary key-value labels for filtering and organization |

### Schema
| Field | Description |
|---|---|
| `num_columns` | Total number of columns/features |
| `columns` | Ordered list of column descriptors (see below) |
| `schema_hash` | Hash of `(name, dtype)` tuples in order; changes when schema changes |

Each entry in `columns` contains:

- `name`: column identifier.
- `dtype`: storage type (`int64`, `float32`, `string`, `bool`, `datetime64`, `bytes`, etc.).
- `nullable`: whether null values are structurally allowed.

### Volume
| Field | Description |
|---|---|
| `num_rows` | Total number of records |

### Per-Column Statistics
Stored under `column_stats[<name>]` for each column.

**For numeric columns** (`int*`, `float*`):

- `null_count`, `null_fraction`.
- `mean`, `std`, `min`, `max`.
- `percentiles`: `{p5, p25, p50, p75, p95, p99}`.
- `histogram`: `{bin_edges: [...], counts: [...]}` (fixed number of bins, e.g. 50).
- `num_unique`: exact count if cheap, otherwise sketch estimate.

**For string columns**:

- `null_count`, `null_fraction`.
- `num_unique`: cardinality.
- `top_values`: top-K `{value: count}` pairs (e.g. K=50).
- `top_values_coverage`: fraction of rows covered by `top_values`.
- `avg_char_length`, `min_char_length`, `max_char_length`.
- `avg_token_count`, `min_token_count`, `max_token_count` (whitespace-split).
- `detected_languages`: top detected language codes with fractions.

**For datetime columns**:

- `null_count`, `null_fraction`.
- `min`, `max`: ISO-8601 boundaries.
- `range_days`: `(max - min)` in days.

**For embedding / vector columns**:

- `null_count`, `null_fraction`.
- `dim`: vector dimensionality.
- `norm_mean`, `norm_std`: statistics of L2 norms.

### Data Quality Signals
| Field | Description |
|---|---|
| `duplicate_row_fraction` | Estimated fraction of exact-duplicate rows |
| `near_duplicate_estimate` | Approximate fraction of near-duplicate rows via MinHash/LSH |
| `constant_columns` | List of column names with zero variance / single unique value |
| `high_null_columns` | List of columns where `null_fraction > 0.5` |

### Content Fingerprints & Sketches
These compact structures enable cheap diff and similarity queries across snapshots without loading raw data.

| Field | Description |
|---|---|
| `row_minhash` | MinHash signature of the row set; enables Jaccard similarity between snapshots |
| `row_hyperloglog` | HyperLogLog sketch for cardinality estimation of unique rows |

### Lineage
| Field | Description |
|---|---|
| `parent_snapshot_id` | ID of the previous snapshot of the same dataset, if any |
| `pipeline_stage` | Where in the pipeline this lives: `raw`, `cleaned`, `featurized`, `augmented`, `sampled`, `final` |
| `transform_description` | Human-readable description of changes since the parent snapshot |

## Consequences
- Snapshots describe data as-is, with no experiment configuration embedded; the same snapshot can be reused across multiple models or tasks without modification.
- Per-column statistics vary in structure by dtype, so consumers must branch on column type when reading `column_stats`.
- Content fingerprints (MinHash, HyperLogLog) enable cheap cross-snapshot diff and similarity queries without loading raw data, at the cost of being approximate.
- Scope is intentionally limited to tabular data; non-tabular modalities (images, audio, video, graphs) are not supported and would require a new ADR to extend the schema.
- `format_version` is required on every snapshot to allow the schema to evolve without breaking existing readers.
