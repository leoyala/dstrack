---
icon: lucide/git-compare
---

# ADR-0004: Diff semantics and severity classification

## Status
:lucide-circle-check: Accepted

## Context
[ADR-0001](0001-dataset-snapshot-content.md) defines what a snapshot contains and
[ADR-0003](0003-local-snapshot-store-layout.md) where snapshots live. Neither says what
it means for two of them to *differ*, which is the whole of the roadmap's "Diff Engine"
milestone and the question `dstrack diff` exists to answer.

Comparing two snapshots is not one problem but three, and they cannot be answered the
same way:

1. **Structural.** Is a column still there, and does it still hold the same type? This
   is exact: nothing is inferred and nothing is thresholded.
2. **Scalar.** A number moved - is that worth telling anyone? Every answer here is a
   judgement against a threshold, and the right threshold depends on what kind of
   number it is.
3. **Distributional.** Do the values still fall where they used to? No single scalar
   answers this, and what *can* be asked depends on which statistics the snapshot
   happened to record.

These choices are expensive to revisit once users depend on them. A threshold that
changes silently makes yesterday's clean run today's alarm, and a JSON key that changes
meaning breaks every pipeline reading it.

## Decision

### A single severity vocabulary
Every comparison, at every level, is classified as one of `unchanged`, `info`,
`warning`, or `critical`. Groups fold upward: a column is as serious as its most serious
measurement, and the diff is as serious as its most serious column. One vocabulary means
the terminal, the JSON and the PDF all rank things identically, and a caller can filter
on severity without knowing what produced it.

Structural severities are fixed rather than thresholded, because their consequences are:

| Change | Severity | Why |
|---|---|---|
| Column removed | `critical` | Anything reading it by name breaks outright. |
| Column type changed | `critical` | Readers break, and the old statistics stop being comparable. |
| Column added | `warning` | Notable, but a consumer that ignores it keeps working. |
| Nullability changed | `info` | A contract change with no immediate breakage. |
| Joined `constant_columns` / `high_null_columns` | `warning` | A feature just went dead or mostly-null. |
| Left either set | `info` | Good news, reported without raising the alarm. |

### Relative thresholds for counts, absolute for fractions
A count is judged by its relative change; a quantity already expressed as a fraction of
one is judged by how far it moved in absolute terms.

This is not a stylistic split. A null fraction going from 0.001 to 0.002 has doubled and
means nothing, and grading it relatively would fire an alarm on two rows in a million.
Conversely a row count going from 1,000 to 1,200 is a 20% change whatever the absolute
figure, and grading it absolutely would need a different threshold for every dataset.

A value moving from exactly zero to something has no defined relative scale, and is
classified `warning`: structurally notable, ungradeable.

Defaults: 10% warns and 50% is critical for relative change; 0.02 and 0.10 absolute for
fractions. Three named presets - `low`, `normal`, `high` - move all thresholds together,
and are the only sensitivity control the CLI exposes. Individual thresholds are available
to the Python API through `DiffOptions`; a dozen float flags would be an unusable
command line.

### Distribution shift: PSI, with a percentile fallback
For numeric columns the measure is the **population stability index** over the recorded
histograms, using the industry-standard bands (0.10 warns, 0.25 is critical).

The two histograms almost never share bin edges, because each snapshot derives its edges
from its own minimum and maximum. Counts cannot be re-binned exactly, so each source
bin's count is spread across a shared grid **in proportion to overlap length** - assuming
uniform density within a bin. This is an approximation, and it can distort the score for
a heavy-tailed column summarized in twenty equal-width bins. It is accepted because it
needs no dependency, because the alternative of refusing to compare unaligned histograms
would refuse almost every real comparison, and because the method is always reported
alongside the score.

Zero counts are smoothed by crediting every bucket **half an observation** (the Jeffreys
prior) rather than by a fixed floor. A fixed floor treats an empty bucket in a four-row
snapshot as being as certainly empty as one in a four-million-row snapshot, and reports
the first as near-total drift; half an observation is a large share of a tiny sample and
a negligible share of a large one, which is exactly how confident the emptiness is.

When a histogram cannot describe a shape at all - too few bins, or the single degenerate
bin a constant column produces - the fallback is the largest percentile move, scaled by
the baseline's p5-p95 spread. The result carries `ShiftMethod.PERCENTILE_SHIFT` so the
score is never read on the PSI scale.

### Distribution shift for text: total variation distance
Text columns record only their top values, so the distance is computed over the union of
both sides' top values **plus a synthetic bucket carrying the untracked tail**
(`1 - top_values_coverage`). Without that bucket, a column whose top values held steady
while everything beneath them churned would score zero.

### No rename inference
A renamed column is reported as one removal and one addition.

Snapshots carry no row-level identity, so the only available heuristic is "same dtype and
similar statistics" - which cannot distinguish a rename from a genuine drop-and-add of
two similar features. The cost of the two errors is not symmetric: reporting a rename as
a drop-and-add is verbose but true, while reporting a drop-and-add as a rename silently
corrupts the per-column drift table, which is the part users act on. `renamed` remains
available as a purely additive key should snapshots ever carry column identity.

### Reference resolution: id, then path, then prefix
`dstrack diff` reads each argument as a full id first, a tracked path second, and a short
id prefix last.

The id-before-path precedence is inherited from `dstrack log` and exists for the same
reason: reading a path first would let any file in the current directory shadow a real
id. The prefix comes *after* the path so a file whose name happens to be eight hex
characters keeps the meaning `log` already gives it, and *before* the final failure so a
mistyped path is reported as a path rather than as an unknown id.

Naming one side leaves the other implied - a snapshot alone is compared against the one
it succeeded - because that is the question most often asked.

### Comparing two datasets is legal
Two snapshots from different datasets compare normally. The result carries
`same_dataset: false` and every renderer says so, but nothing is refused: comparing a
staging table against a production one is a real use, and the engine cannot know which
comparisons are meaningful.

### Snapshot format versions: refuse only what cannot be read
Each side's `format_version` is checked against the set of versions this dstrack can
read (`SUPPORTED_FORMAT_VERSIONS`), not against the one version it writes. Only a
version outside that set is refused, which in practice means a snapshot written by a
newer dstrack: guessing at a shape the engine has never seen would produce a diff that
looks authoritative and is not. A payload recording no version is compared as far as its
fields allow, like any other hand-built dict.

Matching the written version exactly would be simpler and wrong. Snapshots stay in a
store indefinitely, so the first format bump would make every existing snapshot
incomparable - with each other and with the new ones - and the baseline-versus-latest
comparison users most want after an upgrade would be the one refused. A format bump
therefore **adds** to the supported set rather than replacing its member, and comes with
whatever lets the engine read the older shape, whether that is the extractors reading
both or an upgrade step applied to old payloads before comparison. Two snapshots in
different supported versions compare normally.

This is a different version from the one below. `format_version` describes the
snapshot the engine reads; `diff_format_version` describes the result it writes. The
two move independently, and only the first ever causes a refusal.

### `diff_format_version` and an additive-only promise
The JSON output carries `diff_format_version`, currently `"1"`. Within a version, keys
may be **added** but are never renamed, removed, or given a new meaning. Consumers are
expected to ignore keys they do not recognize.

### Tolerating what a payload does not carry
Every field is read defensively; a missing or malformed one degrades to "not recorded"
rather than raising. A metric neither side recorded is omitted from the result entirely.

This is what lets the engine accept dictionaries a caller built by hand, and it is also
why the diff does **not** require `near_duplicate_estimate`, `row_minhash` or
`row_hyperloglog`. Those are listed as required in `snapshot_v1.json` but no current
builder produces them; the gap is pre-existing and out of scope here. Nobody should
"fix" the diff engine to demand them.

Which statistics variant a column holds is inferred from **the keys actually present**,
falling back to the declared dtype. Where the two disagree the keys win: the statistics
are what is about to be compared arithmetically, and trusting a dtype that says
`float64` over an entry carrying `top_values` would read a dictionary as a number.

## Consequences

- The severity vocabulary and the default thresholds are now a compatibility surface.
  Changing a default changes what an unchanged pipeline reports, and is a breaking
  change in everything but name.
- PSI scores are comparable across dstrack versions only as long as the re-binning and
  smoothing are unchanged. Both are recorded here for that reason.
- Every snapshot format bump has to keep the engine able to read the previous format.
  Dropping a version from `SUPPORTED_FORMAT_VERSIONS` strands every stored snapshot
  written in it, so a version can only leave the set once migration tooling can rewrite
  those snapshots.
- A rename shows up as two changes and will look noisier than users expect. This is a
  deliberate trade and should be documented wherever the diff output is explained.
- The diff engine depends on nothing beyond the standard library, so it stays importable
  anywhere `dstrack` is. Only the PDF report needs the optional `report` extra, and it is
  imported lazily so its absence cannot break the command.
