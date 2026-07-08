"""Resolution of the local store's directory, `STORE_DIRNAME/`.

Mirrors how git resolves `.git/`: absent an explicit override, it is found by
walking up from the current directory until a `STORE_DIRNAME/` directory turns up.
"""

import os
from pathlib import Path
from typing import Final

from dstrack.errors import StoreNotFoundError
from dstrack.utils import get_invocation_path

STORE_DIRNAME: Final = ".dstrack"
ROOT_PATH_ENV_VAR: Final = "DSTRACK_ROOT_PATH"


def _find_store_root(start: Path) -> Path:
    """Walk upwards from `start` looking for a `STORE_DIRNAME/` directory.

    Stops, like git, at the filesystem root or as soon as it would cross
    onto a different filesystem/mount than `start`, rather than wandering
    indefinitely upwards into unrelated system directories.

    Args:
        start: Directory to start the search from.

    Returns:
        The path to the `STORE_DIRNAME/` directory itself, the first one found
        at or above `start`.

    Raises:
        StoreNotFoundError: If no directory at or above `start`, up to the
            nearest filesystem boundary, contains a `STORE_DIRNAME/` directory.
    """
    current = start.resolve()
    boundary_dev = current.stat().st_dev

    while True:
        candidate = current / STORE_DIRNAME
        if candidate.is_dir():
            return candidate

        parent = current.parent
        if parent == current or parent.stat().st_dev != boundary_dev:
            break
        current = parent

    raise StoreNotFoundError(
        f"Could not find a `{STORE_DIRNAME}/` directory in `{start}` or any "
        "of its parent directories up to the filesystem boundary. Run "
        "`dstrack init` to create one, or point at one explicitly via "
        f"`--root` or `{ROOT_PATH_ENV_VAR}`."
    )


def resolve_store_root(root: Path | str | None = None) -> Path:
    """Resolve the path to the local store, `STORE_DIRNAME/`.

    Checked in order, the first one available wins:
        1. `root`, e.g. forwarded from a CLI `--root` option: the directory
           `STORE_DIRNAME/` lives (or will be created) in.
        2. The `DSTRACK_ROOT_PATH` environment variable: same meaning as
           `root`.
        3. Walking upwards from the current working directory until a
           `STORE_DIRNAME/` directory is found, the same way git resolves `.git/`.

    Args:
        root: Explicit parent directory of `STORE_DIRNAME/`, typically supplied
            by a CLI `--root` option. Taken as-is, without checking that
            `STORE_DIRNAME/` actually exists inside it.

    Returns:
        The path to the `STORE_DIRNAME/` directory itself, as an absolute path.

    Raises:
        StoreNotFoundError: `root` is `None`, `DSTRACK_ROOT_PATH` is
            unset, and no `STORE_DIRNAME/` directory is found by walking up from
            the current directory.
    """
    if root is not None:
        return Path(root).expanduser().resolve() / STORE_DIRNAME

    env_root = os.environ.get(ROOT_PATH_ENV_VAR)
    if env_root:
        return Path(env_root).expanduser().resolve() / STORE_DIRNAME

    return _find_store_root(get_invocation_path())
