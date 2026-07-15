"""Registry of reader classes, keyed by short name and by file extension.

Registration is the single extension point for readers. Three kinds of user
reach it by three different routes, and none of them should have to do the other
two's work:

- **From Python**, a caller who already has a reader instance hands it straight
  to [SnapshotBuilder][dstrack.snapshot._builder.SnapshotBuilder]; the registry
  is never consulted.
- **From an installed plugin package**, a reader self-registers through the
  ``dstrack.readers`` entry-point group, so ``dstrack track data.parquet`` infers
  it from the extension with nothing to type.
- **From an ad-hoc class** in the user's own project, which is not installed as a
  plugin and so is named explicitly as ``"package.module:ClassName"`` (see
  [dstrack.readers._resolve][]).

A registered class must satisfy both
[TabularReader][dstrack.readers._protocol.TabularReader] (how it reads) and
[ReaderFactory][dstrack.readers._protocol.ReaderFactory] (how it is built from a
path);
[check_reader_class()][dstrack.readers._registry.check_reader_class] enforces
that *before* the class is ever instantiated.
"""

import inspect
import logging
from collections.abc import Sequence
from importlib.metadata import entry_points
from pathlib import Path
from typing import Final

from ._csv import CsvReader
from ._protocol import ReaderFactory, TabularReader

_log = logging.getLogger(__name__)

# Entry-point group an installed package declares to add a reader, e.g.
#   [project.entry-points."dstrack.readers"]
#   parquet = "dstrack_parquet:ParquetReader"
ENTRY_POINT_GROUP: Final[str] = "dstrack.readers"

# Short name (as typed for --reader) -> reader class.
_BY_NAME: dict[str, type[TabularReader]] = {}
# Lowercase file extension, leading dot included -> reader class.
_BY_EXTENSION: dict[str, type[TabularReader]] = {}

_plugins_loaded = False


def check_reader_class(obj: object, *, origin: str) -> type[TabularReader]:
    """Validate that ``obj`` is a class usable as a reader, without calling it.

    Both protocols are checked here, on the class, so that a class which merely
    *looks* like a reader is rejected before its constructor runs on a user path.

    Args:
        obj: The candidate, typically freshly imported or freshly registered.
        origin: How the caller referred to it (a spec, a name, an entry point),
            used to make the error message actionable.

    Returns:
        ``obj`` itself, narrowed to a reader class.

    Raises:
        TypeError: If ``obj`` is not a class, or does not satisfy
            [TabularReader][dstrack.readers._protocol.TabularReader] and
            [ReaderFactory][dstrack.readers._protocol.ReaderFactory].
    """
    if not isinstance(obj, type):
        raise TypeError(
            f"{origin} resolved to {obj!r}, which is not a class. "
            "A reader must be a class, not an instance or a function."
        )
    qualname = f"{obj.__module__}.{obj.__qualname__}"
    if not issubclass(obj, TabularReader):
        raise TypeError(
            f"{qualname} (from {origin}) does not satisfy the TabularReader "
            "protocol: it must define columns() and iter_batches()."
        )
    # Checked through an object-typed name: mypy assumes any class object
    # already satisfies ReaderFactory, so narrowing from `object` is what keeps
    # this branch live for the type checker as well as at runtime.
    candidate: object = obj
    if not isinstance(candidate, ReaderFactory):
        raise TypeError(
            f"{qualname} (from {origin}) does not satisfy the ReaderFactory "
            "protocol: it must define a from_path(path) classmethod, which is "
            "how dstrack builds a reader it knows only by name."
        )
    # Check that from_path is a class method
    if not isinstance(inspect.getattr_static(obj, "from_path"), classmethod):
        raise TypeError(
            f"{qualname} (from {origin}) defines from_path, but not as a "
            "classmethod: dstrack calls it on the class (MyReader.from_path(path)), "
            "so it must be decorated with @classmethod."
        )
    return obj


def build_reader(reader_cls: type[TabularReader], path: Path) -> TabularReader:
    """Instantiate a validated reader class for ``path``.

    Args:
        reader_cls: A class already validated through
            [check_reader_class()][dstrack.readers._registry.check_reader_class].
        path: Path to the dataset source.

    Returns:
        The reader instance.

    Raises:
        TypeError: If ``reader_cls`` was never validated and lacks ``from_path``,
            or if its ``from_path`` returns something that is not a
            [TabularReader][dstrack.readers._protocol.TabularReader].
    """
    factory: object = reader_cls
    if not isinstance(factory, ReaderFactory):
        raise TypeError(f"{reader_cls!r} is not an instance of {ReaderFactory}")
    reader: object = factory.from_path(path)
    if not isinstance(reader, TabularReader):
        qualname = f"{reader_cls.__module__}.{reader_cls.__qualname__}"
        raise TypeError(
            f"{qualname}.from_path returned {reader!r}, which is not a "
            "TabularReader: from_path must build an object that follows this protocol."
        )
    return reader


def register_reader(
    reader_cls: type[TabularReader],
    *,
    name: str,
    extensions: Sequence[str] | None = None,
) -> None:
    """Register a reader under a short name and the extensions it handles.

    Args:
        reader_cls: The reader class. Must satisfy both
            [TabularReader][dstrack.readers._protocol.TabularReader] and
            [ReaderFactory][dstrack.readers._protocol.ReaderFactory].
        name: Short name, as typed for ``--reader`` (e.g. ``"csv"``).
        extensions: Extensions this reader claims, leading dot included. When
            omitted, the class's ``EXTENSIONS`` attribute is used, so a plugin
            can declare its extensions once on the class and register through a
            bare entry point.

    Raises:
        TypeError: If ``reader_cls`` does not satisfy both reader protocols.
        ValueError: If ``name`` or any extension is already taken. Registration
            never silently displaces an existing reader: two packages fighting
            over ``.parquet`` is a conflict the user has to see, not one to
            resolve by import order.
        ValueError: If any extension is malformed. An extension is matched
            against [Path.suffix][pathlib.PurePath.suffix], so it must be a
            single leading-dot suffix such as ``".csv"``: a value like ``"csv"``
            or ``".tar.gz"`` could never match and is rejected rather than
            silently registered as dead weight.
    """
    check_reader_class(reader_cls, origin=f"reader {name!r}")

    if extensions is None:
        extensions = getattr(reader_cls, "EXTENSIONS", ())

    if name in _BY_NAME:
        raise ValueError(
            f"Reader name {name!r} is already registered to "
            f"{_BY_NAME[name].__module__}.{_BY_NAME[name].__qualname__}."
        )

    claimed = [ext.lower() for ext in extensions]
    for ext in claimed:
        # An extension is only ever compared against Path.suffix, which is a
        # single leading-dot component (".csv").
        if not ext.startswith(".") or ext == "." or Path(f"_{ext}").suffix != ext:
            raise ValueError(
                f"Extension {ext!r} (for reader {name!r}) is malformed: an "
                "extension must be a single leading-dot suffix such as '.csv', "
                "so that it can match Path.suffix."
            )
    for ext in claimed:
        if ext in _BY_EXTENSION:
            owner = _BY_EXTENSION[ext]
            raise ValueError(
                f"Extension {ext!r} is already registered to "
                f"{owner.__module__}.{owner.__qualname__}."
            )

    _BY_NAME[name] = reader_cls
    for ext in claimed:
        _BY_EXTENSION[ext] = reader_cls


def _load_plugins() -> None:
    """Register readers advertised by installed packages, once per process.

    A plugin that fails to import or conflicts with an existing registration is
    logged and skipped: one broken third-party package must not take down
    `dstrack track` for a dataset that does not even use it.
    """
    global _plugins_loaded
    if _plugins_loaded:
        return
    # Set before loading: a plugin that raises must not be retried on every
    # subsequent lookup.
    _plugins_loaded = True

    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        try:
            register_reader(entry_point.load(), name=entry_point.name)
        except Exception as e:
            _log.warning(
                f"Ignoring reader plugin {entry_point.name!r} "
                f"({entry_point.value}): {e}"
            )


def reader_class_for_name(name: str) -> type[TabularReader] | None:
    """Look up a reader by its short name, or ``None`` if nothing claims it."""
    _load_plugins()
    return _BY_NAME.get(name)


def reader_class_for_extension(extension: str) -> type[TabularReader] | None:
    """Look up a reader by file extension, or ``None`` if nothing claims it."""
    _load_plugins()
    return _BY_EXTENSION.get(extension.lower())


def available_readers() -> dict[str, type[TabularReader]]:
    """Return all registered readers by short name, built-in and plugin alike."""
    _load_plugins()
    return dict(_BY_NAME)


def known_extensions() -> dict[str, type[TabularReader]]:
    """Return all registered extensions and the reader class that claims each."""
    _load_plugins()
    return dict(_BY_EXTENSION)


register_reader(CsvReader, name="csv")
