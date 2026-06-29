---
icon: lucide/ban
---

# ADR-0002: `bytes` dtype is not supported in CsvReader

## Status
:lucide-circle-check: Accepted

## Context
ADR-0001 lists `bytes` as a valid `dtype` value in the snapshot schema's column
descriptor (`dtype` field, Schema section).  That document describes the
*snapshot schema*, which is the serialisation format shared by all readers.  It does not
imply that every reader can infer or emit a `bytes` column.

CSV is a plain-text format.  A `bytes` column has no canonical text
representation: hex strings (`0xDEAD`), Base64, percent-encoding, and raw
Latin-1 bytes are all plausible encodings, and none is universally adopted.
Auto-detecting which encoding a column uses from sample values is error-prone
(SHA-1 hashes, UUIDs, and random strings all look like Base64 or hex), and
silently picking the wrong encoding produces corrupted data without raising an
error.

## Decision
`CsvReader` will not support the `bytes` dtype, either through auto-inference or
through the `column_dtypes` override parameter.  Passing `"bytes"` as a dtype
override raises a `ValueError` immediately, rather than silently returning
strings or corrupted binary data.

Binary data in a CSV pipeline should be handled by:

1. Keeping the column as `string` and decoding it explicitly in application code
   with the correct codec (`bytes.fromhex(...)`, `base64.b64decode(...)`, etc.).
2. Switching to a binary-native format (Parquet, HDF5, Arrow IPC) and using a
   dedicated reader once one is available.

This decision does **not** affect the snapshot schema in ADR-0001; `bytes`
remains a valid dtype token for readers that can natively handle binary columns
(e.g. a future ParquetReader).

## Consequences
- `CsvReader` raises `ValueError` if `"bytes"` appears in `column_dtypes`.
- Binary columns in CSV files that were previously kept as `string` continue to
  work unchanged. The dtype will still be inferred as `string`.
- The restriction is isolated to `CsvReader`; other readers are free to support
  `bytes` when their source format makes the encoding unambiguous.
