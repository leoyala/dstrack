"""Single source of truth for the snapshot format version.

Bump [FORMAT_VERSION][dstrack.snapshot._version.FORMAT_VERSION] and
[SCHEMA_PATH][dstrack.snapshot._version.SCHEMA_PATH] together whenever
[SnapshotMetadata][dstrack.snapshot._metadata.SnapshotMetadata]'s shape changes.

A bump must not drop the old version from
[SUPPORTED_FORMAT_VERSIONS][dstrack.snapshot._version.SUPPORTED_FORMAT_VERSIONS]:
snapshots already in a store keep the version they were written in, and
removing it would make every one of them unreadable after an upgrade. Add the
new version alongside it, and teach the readers to handle both.
"""

from pathlib import Path
from typing import Final

FORMAT_VERSION = "1"
SCHEMA_PATH = (
    Path(__file__).parent.parent / "schemas" / f"snapshot_v{FORMAT_VERSION}.json"
)

# Every format version this dstrack can read, which is the one it writes plus
# each older one it still understands. A version outside this set was written by
# a newer dstrack, or is not a dstrack snapshot at all.
SUPPORTED_FORMAT_VERSIONS: Final[frozenset[str]] = frozenset({FORMAT_VERSION})
