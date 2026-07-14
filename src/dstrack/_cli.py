import logging
import tempfile
from pathlib import Path
from typing import Annotated

import typer

from dstrack import __version__, console
from dstrack._store_template import STORE_TEMPLATE, materialize
from dstrack._track import track
from dstrack.errors import StoreInitError
from dstrack.utils import get_invocation_path

_log = logging.getLogger(__name__)

app = typer.Typer(
    no_args_is_help=True,
    epilog="Made with :heart:",
    suggest_commands=True,
    pretty_exceptions_show_locals=False,
    help="Main CLI interface to `dstrack`'s capabilities.",
)


def init_local_store() -> Path:
    """Initializes local store structure.

    Builds [STORE_TEMPLATE][dstrack._store_template.STORE_TEMPLATE] in a
    temporary directory next to the destination, and only moves it into place
    once every file and directory in the template has been created successfully.
    The destination is therefore never touched by a failed or partial build.

    Returns:
        Path where the local store was created.

    Raises:
        FileExistsError: If the local store path already exists.
        StoreInitError: If building the store from its template fails. The
            temporary build directory is cleaned up before raising.
    """
    invocation_path = get_invocation_path()
    store_path = invocation_path / STORE_TEMPLATE.name

    if store_path.is_dir():
        raise FileExistsError(f"Local store path already exists: {store_path}")

    with tempfile.TemporaryDirectory(
        prefix=f"{STORE_TEMPLATE.name}-tmp-", dir=invocation_path
    ) as tmp_dir:
        try:
            built_path = materialize(STORE_TEMPLATE, Path(tmp_dir))
        except Exception as e:
            raise StoreInitError(
                f"Failed to initialize local store at {store_path}"
            ) from e

        console.info(f"Generating local store structure at {store_path}.")
        built_path.rename(store_path)

    return store_path


@app.command(
    help="Initialize local store before starting to track any dataset. "
    "The local store `.dstrack/` is created at the location where this "
    "command is called."
)
def init(
    allow_exists: Annotated[
        bool, typer.Option(help="Do not fail if `.dstrack/` already exists.")
    ] = False,
) -> None:
    try:
        store_path = init_local_store()
        console.success(f"Finished creating local store: {store_path}")
    except FileExistsError as e:
        if not allow_exists:
            console.error(str(e))
            raise
        _log.warning(f"{e}")
        console.warning(f"{e}")
    except StoreInitError as e:
        _log.error(f"Failed to create local store. {e}")
        console.error(str(e))
        raise


app.command(help="Compute a snapshot of a dataset and store it in the local store.")(
    track
)


@app.command(help="Print package version.")
def version() -> None:
    print(__version__)


if __name__ == "__main__":
    app()
