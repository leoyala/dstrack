---
icon: lucide/folder-tree
---

# ADR-0003: Local snapshot store layout

## Status
:lucide-circle-check: Accepted

## Context
[ADR-0001](0001-dataset-snapshot-content.md) defines what a snapshot contains but not
where snapshots live once they're taken. The roadmap's "Local snapshot store" milestone
needs a concrete answer to four questions:

1. Where does the store live, and is it something a team shares via git or something
   private to each machine?
2. How are snapshots grouped by dataset, and how does a dataset get identified across
   renames and moved files?
3. How can a user quickly search across many tracked datasets, or view the history of
   one, without reading every snapshot's full payload?
4. How does `dstrack snapshot` know which dataset a given file belongs to, given that
   the same logical dataset may live at different paths on different machines?

A key property, established by ADR-0001, shapes the answer to all four: a snapshot
holds no raw dataset content, only semantic metadata, statistics, and sketches. Files
are small and text-based, which makes them safe and useful to commit to git, unlike
tools such as DVC or git-LFS, which snapshot actual data blobs and must keep those out
of git.

## Decision

### Location
The store lives at `.dstrack/` in the project root, resolved by walking up from the
current working directory the same way git resolves `.git/`, subcommands work from
any subdirectory. `dstrack init` creates it explicitly.

Because snapshots carry no raw data, `.dstrack/` is committed to git by default, not
gitignored. This is what makes `dstrack status`/`--fail-on-drift` in CI and PR-level
drift review possible: dataset history travels with the code that produced it. A small
number of files inside `.dstrack/` are the exception, see
[What's committed vs. local-only](#whats-committed-vs-local-only) below.

### Directory layout

```
.dstrack/
├── .gitignore                        # excludes cache/
├── cache/
│   └── index.db                      # gitignored: disposable search accelerator
└── datasets/
    └── <dataset_id>/                 # dataset_id: UUID4, minted at first snapshot, permanent
        ├── HEAD                      # snapshot_id of the latest snapshot
        ├── log.jsonl                 # append-only lightweight history, one line per snapshot
        └── snapshots/
            └── <snapshot_id>.json    # full snapshot payload; validates against corresponding json schema
```

### Dataset identity: an immutable `dataset_id`
A dataset is identified by a `dataset_id`: a UUID4 minted the first time a given
dataset is snapshotted, used as-is as its directory name under `datasets/`. It never
changes for the life of the dataset, and nothing about it is derived from
`dataset_name` or from the file's path, unlike a slug, it survives both the source
file being renamed/moved and the dataset's human-readable name changing later, since
neither feeds into it.

Proposed behavior: recording the path as per-snapshot history rather than a fixed pointer
(see [Path resolution](#path-resolution-relative-root-overridable-never-a-shared-pointer)
below) requires an identity that is neither the path nor a display name that may
reasonably change; a random, permanent `dataset_id` is the simplest thing that
satisfies both.

`snapshot_id` remains a separate UUID4 for each snapshot, exactly as specified in
ADR-0001; `dataset_id` groups all of a dataset's snapshots together.

### History and search: what's derived, what's not
Two different jobs need two different mechanisms:

- **History of one dataset** (`dstrack log <dataset_id>`) only ever reads that
  dataset's own `snapshots/`, so no index is needed to make it fast. `dstrack log <path>`
  is also accepted, resolved to a `dataset_id` via the same path-matching used by
  `dstrack snapshot` (see [Path resolution](#path-resolution-relative-root-overridable-never-a-shared-pointer)); if
  the path doesn't exactly match any dataset's last recorded one, `--dataset-id` is
  required instead.
- **Search across many datasets** (`dstrack search ...`, by name/tag/pipeline stage)
  would otherwise mean opening every dataset's snapshot JSON just to read its name or
  tags, files that may also contain large histograms and MinHash/HyperLogLog sketches
  (see ADR-0001). Search needs a cheap, lightweight path.

`log.jsonl` is the fix for both, and is committed to git. Every `dstrack snapshot` call
writes the full snapshot JSON, appends one line to `log.jsonl` with only the lightweight
identity fields (`snapshot_id`, `parent_snapshot_id`, `created_at`, `created_by`,
`dataset_name`, `dataset_path`, `description`, `tags`, `num_rows`,
`num_columns`), and atomically updates `HEAD`, all three as one operation, so they can
never drift apart.
Anyone who clones or pulls the repo has identical, correct history immediately, with no
rebuild step.

`cache/index.db` (SQLite, stdlib `sqlite3`, no new dependency) is a pure performance
accelerator for cross-dataset search, built by reading the small `log.jsonl` files,
never the heavy snapshot JSON. It is **not** a second source of truth: it is gitignored
and safe to delete at any time. Every `dstrack snapshot` call appends its new row
straight to the index, and every `dstrack search` also stats each dataset's `log.jsonl`
first and re-syncs any lines it's missing (size/mtime tracked per dataset), so
first-use, deletion, and pulls from other machines are all covered too. Between the
two, a search can never see stale results, only, in the worst case, a slower one while
it resyncs.

### Path resolution: relative, root-overridable, never a shared pointer
Every snapshot's `dataset_path` (ADR-0001) is stored relative to a *path root*, always
written with `/` separators regardless of OS, and interpreted back with
`pathlib.Path` so it round-trips correctly cross-platform. The path root defaults to
the store root (the directory `.dstrack/` was found in, by walking up from cwd);
`dstrack snapshot --root <dir>` overrides it for a single invocation, for cases where
the data doesn't live under the checkout at all (a separate mount, a CI temp
directory, a per-environment bucket).

Critically, `dstrack` never persists a single `the path for dataset "X"` pointer
anywhere. That value can legitimately differ by machine, and a shared, committed
pointer would just mean two contributors fighting over the same file every time their
layouts disagree. Instead, every snapshot honestly records the relative path used
*for that run* in that dataset's own `log.jsonl`; there is nothing to reconcile, only
history to append to. Nothing needs to be declared ahead of time, and there is no
default/override split to maintain.

This also answers how `dstrack snapshot <path>` knows which dataset a file belongs to:
it computes the candidate relative path and compares it against
each dataset's most recent (`HEAD`) `log.jsonl` entry. An exact match continues that
dataset's lineage. No match means either a genuinely new dataset,
or the same dataset seen from a path that doesn't match its
last recorded one, a renamed/moved file, or a first snapshot on a machine with a
different layout for an already-tracked dataset. Both look identical to `dstrack` and
are resolved the same way: pass `--dataset-id <uuid>` explicitly (found via
`dstrack log`/`dstrack search`) to say `"this is a continuation, not a new dataset."`

### Snapshot write path
Given `dstrack snapshot <path> [--name NAME] [--root DIR] [--dataset-id ID]`:

1. Resolve the store root by walking up from cwd to find `.dstrack/`.
2. Resolve the path root: `--root` if given, otherwise the store root. Compute
   `dataset_path` as `<path>` relative to the path root, in POSIX form.
3. Resolve the dataset:
   - `--dataset-id` given: use that dataset directly; its lineage continues even
     though this run's `dataset_path` may differ from its predecessor's.
   - otherwise, look for a dataset whose `HEAD` snapshot's `dataset_path` exactly
     matches the one just computed. Match: continue its lineage. No match: this is a
     new dataset, `--name` is required, and a new `dataset_id` (UUID4) is minted.
4. If continuing an existing lineage, read `HEAD` and use it as `parent_snapshot_id`;
   otherwise `parent_snapshot_id` is `null`.
5. Read the source with the reader inferred from the file extension, or with the one
   named by `--reader` (see [Readers are chosen per invocation, never
   persisted](#readers-are-chosen-per-invocation-never-persisted) below), compute
   schema/stats/hashes per ADR-0001, and mint a new `snapshot_id` (UUID4).
6. Write `snapshots/<snapshot_id>.json`, append the corresponding line to `log.jsonl`
   (including `dataset_path`), then atomically replace `HEAD` (write-temp + rename),
   in that order, so a crash never leaves `HEAD` pointing at a snapshot that wasn't
   fully written.

### Readers are chosen per invocation, never persisted
A reader is resolved fresh on every `dstrack snapshot` call: inferred from the file's
extension, or named explicitly by `--reader`, which accepts either a registered reader
name (`--reader csv`) or a `package.module:ClassName` spec that `dstrack` imports
directly (`--reader mypackage.readers:ExcelReader`).

**The resolved reader is deliberately not part of a snapshot.** No `reader` field is
written to `snapshots/*.json` or to `log.jsonl`, and nothing in the store is ever read
back to decide which reader to use. This is a security boundary, not an oversight:
a `package.module:ClassName` spec is arbitrary import-by-name, which is arbitrary code
execution. That is perfectly safe as an argument the invoking user typed themselves, and
it is exactly how gunicorn, uvicorn and celery accept application objects. It stops being
safe the moment the same string is read from a file, because `.dstrack/` is *committed to
git* (see [Location](#location)) and therefore travels with the repo. A reader spec stored
in a snapshot would mean that cloning a repository and running `dstrack snapshot` executes
an importable path chosen by whoever wrote that commit, turning a pull request into a code
execution vector on every reviewer's and CI runner's machine.

The rule is therefore: **a reader spec may travel from the user to `dstrack`, never from
the store to `dstrack`.** Anything that would need a per-dataset default reader must be
solved another way, by an installed plugin claiming the extension via the
`dstrack.readers` entry-point group (code the user chose to `pip install`, on the same
footing as any other dependency), never by resurrecting an import path out of committed
history.

The cost is real and accepted: a dataset whose extension is not claimed by any installed
reader needs `--reader` on every invocation, and cannot be made to "just work" for a
teammate by committing that fact. Publishing or vendoring the reader as a plugin package
is the supported answer.

### What's committed vs. local-only

| Path | Committed? | Why |
|---|---|---|
| `datasets/<dataset_id>/snapshots/*.json` | Yes | Source of truth for snapshot content |
| `datasets/<dataset_id>/log.jsonl` | Yes | Source of truth for history; small and append-only |
| `datasets/<dataset_id>/HEAD` | Yes | Small pointer; part of the same atomic write as the above |
| `cache/index.db` | No | Disposable, rebuildable search index |

## Example walkthrough

Snapshot two new datasets. There is no separate registration step, the first
`dstrack snapshot` call for a given file is what creates it:

```
dstrack init
dstrack snapshot data/train.csv --name "Customer Churn"
dstrack snapshot data/catalog.parquet --name "Product Catalog"
```

The first call mints a `dataset_id`, computes `dataset_path` relative to the store
root (`data/train.csv`), and creates:

```
.dstrack/datasets/8f14e45f-ceea-4c6a-8f31-8b0e2e1a9c3f/
├── HEAD                # 8f14e45f-ceea-4c6a-8f31-8b0e2e1a9c3f
├── log.jsonl            # 1 line
└── snapshots/
    └── 8f14e45f-ceea-4c6a-8f31-8b0e2e1a9c3f.json
```

```json title="log.jsonl"
{"snapshot_id":"8f14e45f-ceea-4c6a-8f31-8b0e2e1a9c3f","parent_snapshot_id":null,"created_at":"2026-07-01T14:03:22Z","created_by":"user","dataset_name":"Customer Churn","dataset_path":"data/train.csv","description":"Raw customer churn export from CRM", "tags":{"team":"growth"},"num_rows":50231,"num_columns":12}
```

Later, `data/train.csv` is updated and re-snapshotted, no name needed, since the
dataset already exists:

```
dstrack snapshot data/train.csv
```

`dstrack` computes `dataset_path` (`data/train.csv`), finds it matches the
`HEAD` entry of `datasets/8f14e45f-.../log.jsonl`, so this continues that dataset's
lineage: it reads `HEAD` to get `8f14e45f-...` as the new snapshot's
`parent_snapshot_id`, and writes:

```
.dstrack/datasets/8f14e45f-ceea-4c6a-8f31-8b0e2e1a9c3f/
├── HEAD                # updated → c27b1a90-4e3d-4a9b-9a17-7d6f2e5c9b21
├── log.jsonl            # 2 lines now
└── snapshots/
    ├── 8f14e45f-ceea-4c6a-8f31-8b0e2e1a9c3f.json
    └── c27b1a90-4e3d-4a9b-9a17-7d6f2e5c9b21.json
```

A teammate whose data lives elsewhere runs the same command with a different path,
and once, for their first snapshot on this machine, tells `dstrack` which dataset
it continues, since the path won't auto-match:

```
dstrack snapshot /mnt/shared-data/exports/train_2026_07.csv \
    --root /mnt/shared-data/exports \
    --dataset-id 8f14e45f-ceea-4c6a-8f31-8b0e2e1a9c3f
```

This appends a snapshot with `dataset_path: "train_2026_07.csv"` (relative to their
`--root`) to the exact same `8f14e45f-...` lineage everyone else sees, a fact
recorded in `log.jsonl`, not reconciled against anyone else's path. Their next
snapshot of that same file can drop `--dataset-id`, since it will now match their own
machine's most recent recorded path.

Finally, both `dstrack log 8f14e45f-...` (reads `log.jsonl` directly, three entries,
no `cache/index.db` involved) and `dstrack search --tag pipeline_stage=raw` (built from
`cache/index.db`, rebuilt from `log.jsonl` if the cache is missing or was never built on
this machine) return the same answer regardless of which machine ran them.

## Consequences
- `.dstrack/` is committed to git by default; only `cache/` is gitignored. Cloning the
  repo is sufficient to get full, correct dataset history, no rebuild, fetch, or
  config step required.
- Dataset identity is a permanent `dataset_id` (UUID4), independent of both the file's
  path and the dataset's display name. Either can change over a dataset's life without
  losing lineage; a dataset can also legitimately be renamed (a later snapshot uses a
  different `dataset_name`) without inventing new machinery for it.
- There is no tracked-dataset registry and no local override file. Every snapshot
  requires a path (the thing being snapshotted); a first-time snapshot additionally
  requires `--name`, and a snapshot whose path doesn't match the dataset's last
  recorded one requires `--dataset-id` to say which lineage it continues.
- Two unrelated first-time snapshots that happen to resolve to the same `dataset_path`
  still get distinct `dataset_id's`.
- `log.jsonl` and `snapshots/*.json` must be written together, atomically, by the same
  operation. A store writer that fails to keep them in sync produces an inconsistent
  dataset history; this is an implementation invariant, not just a convention.
- `cache/index.db` can be deleted at any time with no data loss, only a slower next
  search until it's rebuilt.
- No reader information is ever written to the store, and no future field may reintroduce
  it: because `.dstrack/` is committed, an importable path read back out of it would
  execute code chosen by whoever authored the commit. Readers reach `dstrack` from the
  invoking user (`--reader`) or from installed plugins, never from committed history. Any
  proposal for a per-dataset default reader supersedes this ADR rather than extending it.
