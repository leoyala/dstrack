import logging
import shutil
from pathlib import Path
from typing import Annotated

import typer

from dstrack import __version__, console
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


def _init_local_store_gitignore(path: Path) -> None:
    """Create .gitignore file in provided path.

    creates and populates file only if it does not already exists.

    Args:
        path: Path where .gitignore file should be created.

    Raises:
        FileExistsError: When .gitignore file already exists in path.

    Returns:
        None
    """
    file_path = path / ".gitignore"
    if file_path.is_file():
        raise FileExistsError(f"Store .gitignore file already exists: {file_path}")

    # Create and populate .gitignore file
    console.info("Generating .gitignore file in local store.")
    with file_path.open("w"):
        file_path.write_text(".cache/")


def init_local_store() -> Path:
    """Initializes local store structure.

    creates a local store folder called `.dstrack`, a `datasets/` folder
    within it, and a `.gitignore` file to ignore the cache directory.

    Returns:
        Path where the local store was created.

    Raises:
        FileExistsError: If the local store path already exists.
        StoreInitError: If store creation starts but fails partway through,
            e.g. because the `.gitignore` file cannot be written. Any
            partially created state is rolled back before raising.
    """
    store_path = get_invocation_path() / ".dstrack"

    if store_path.is_dir():
        raise FileExistsError(f"Local store path already exists: {store_path}")

    # Create local store path
    store_path.mkdir()
    try:
        # Create dataset snapshots path
        (store_path / "datasets").mkdir()
        _init_local_store_gitignore(path=store_path)
    except Exception as e:
        shutil.rmtree(store_path)
        raise StoreInitError(f"Failed to initialize local store at {store_path}") from e

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


@app.command(help="Print package version.")
def version() -> None:
    print(__version__)


if __name__ == "__main__":
    app()
