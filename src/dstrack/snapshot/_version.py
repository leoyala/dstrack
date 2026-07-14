"""Single source of truth for the snapshot format version.

Bump [FORMAT_VERSION][dstrack.snapshot._version.FORMAT_VERSION] and
[SCHEMA_PATH][dstrack.snapshot._version.SCHEMA_PATH] together whenever
[SnapshotMetadata][dstrack.snapshot._metadata.SnapshotMetadata]'s shape changes.
"""

from pathlib import Path

FORMAT_VERSION = "1"
SCHEMA_PATH = (
    Path(__file__).parent.parent / "schemas" / f"snapshot_v{FORMAT_VERSION}.json"
)
