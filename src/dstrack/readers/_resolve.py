"""Selection and loading of a :class:`TabularReader` for a given source.

A reader is chosen one of three ways, in order of how much the user has to type:

1. Implicitly, from the source file's extension (``data.csv`` -> `CsvReader`).
   Plugin readers registered through the ``dstrack.readers`` entry-point group
   participate here too, so an installed reader needs nothing on the command line.
2. By short name (``"csv"``), for when the extension is absent or misleading.
3. By ``"package.module:ClassName"`` spec, which imports the class directly. This
   is the escape hatch for a reader that lives in the user's own project and is
   not installed as a plugin.

Security: a spec is arbitrary import-by-name, i.e. code execution. It is only
ever accepted from the invoking user (a ``--reader`` argument they typed), and
is never persisted into a snapshot nor read back out of one. See ADR-0003.
"""

import importlib
from pathlib import Path

from ._protocol import TabularReader
from ._registry import (
    available_readers,
    build_reader,
    check_reader_class,
    known_extensions,
    reader_class_for_extension,
    reader_class_for_name,
)


def load_reader_class(spec: str) -> type[TabularReader]:
    """Import a reader class from a ``"package.module:ClassName"`` spec.

    The class is validated against both reader protocols before being returned,
    so a mistyped spec that happens to name some other importable object fails
    here rather than part-way through a snapshot.

    Args:
        spec: A ``"<module>:<class>"`` string. The module part is imported and
            the class part is looked up on it.

    Returns:
        The referenced class (not an instance).

    Raises:
        ValueError: If ``spec`` is not of the form ``"module:ClassName"``, the
            module cannot be imported, or the class is not found on it.
        TypeError: If the referenced object is not a usable reader class.
    """
    module_name, sep, class_name = spec.partition(":")
    if not sep or not module_name or not class_name:
        raise ValueError(
            f"Invalid reader spec {spec!r}. "
            "Expected the form 'package.module:ClassName'."
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise ValueError(
            f"Could not import module {module_name!r} from reader spec {spec!r}."
        ) from e
    try:
        obj = getattr(module, class_name)
    except AttributeError as e:
        raise ValueError(
            f"Module {module_name!r} has no attribute {class_name!r} "
            f"(from reader spec {spec!r})."
        ) from e

    return check_reader_class(obj, origin=f"reader spec {spec!r}")


def resolve_reader_class(
    path: str | Path, *, reader: str | None = None
) -> type[TabularReader]:
    """Choose the reader class for ``path``, without instantiating it.

    Args:
        path: Path to the dataset source.
        reader: Either a registered short name (``"csv"``) or a
            ``"package.module:ClassName"`` spec, told apart by the ``":"``.
            When omitted, the reader is inferred from ``path``'s extension.

    Returns:
        A validated reader class.

    Raises:
        ValueError: If ``reader`` names nothing known, a spec is malformed, or
            no reader is registered for ``path``'s extension.
        TypeError: If the resolved class is not a usable reader.
    """
    if reader is not None:
        if ":" in reader:
            return load_reader_class(reader)
        reader_cls = reader_class_for_name(reader)
        if reader_cls is None:
            known = ", ".join(sorted(available_readers())) or "(none)"
            raise ValueError(
                f"Unknown reader {reader!r}. Available readers: {known}. "
                "Pass 'package.module:ClassName' to use a reader that is not "
                "installed as a plugin."
            )
        return reader_cls

    path = Path(path)
    extension = path.suffix.lower()
    reader_cls = reader_class_for_extension(extension)
    if reader_cls is None:
        known = ", ".join(sorted(known_extensions())) or "(none)"
        raise ValueError(
            f"No reader registered for extension {extension!r} (path: {path}). "
            f"Known extensions: {known}. Name a reader explicitly, either a "
            "registered one or 'package.module:ClassName'."
        )
    return reader_cls


def resolve_reader(path: str | Path, *, reader: str | None = None) -> TabularReader:
    """Build a reader for ``path``, explicitly named or inferred from its extension.

    Args:
        path: Path to the dataset source the reader will read.
        reader: Optional short name or ``"package.module:ClassName"`` spec. When
            omitted, the reader is inferred from ``path``'s file extension.

    Returns:
        An instantiated reader satisfying
        [TabularReader][dstrack.readers.TabularReader].

    Raises:
        ValueError: If ``reader`` names nothing known, a spec is malformed, or
            no reader is registered for ``path``'s extension.
        TypeError: If the resolved class is not a usable reader.
    """
    path = Path(path)
    reader_cls = resolve_reader_class(path, reader=reader)
    return build_reader(reader_cls, path)
