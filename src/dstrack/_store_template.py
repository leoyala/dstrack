"""Declarative description of the local store's directory structure."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from dstrack.paths import STORE_DIRNAME


@dataclass(frozen=True)
class TemplateFile:
    """A file to create, with its literal content."""

    name: str
    content: str = ""


@dataclass(frozen=True)
class TemplateDir:
    """A directory to create, optionally containing files and subdirectories."""

    name: str
    entries: "tuple[TemplateDir | TemplateFile, ...]" = field(default_factory=tuple)


STORE_TEMPLATE: Final[TemplateDir] = TemplateDir(
    name=STORE_DIRNAME,
    entries=(
        TemplateDir(name="datasets"),
        TemplateFile(name=".gitignore", content=".cache/"),
    ),
)


def materialize(template: TemplateDir, parent: Path) -> Path:
    """Recursively create `template`'s directory structure under `parent`.

    Args:
        template: Directory template to materialize, including the
            top-level directory itself.
        parent: Existing directory that `template`'s directory is created in.

    Returns:
        The path created for `template`.

    Raises:
        FileExistsError: If any file or directory in the template already
            exists under `parent`.
    """
    path = parent / template.name
    path.mkdir()
    for entry in template.entries:
        if isinstance(entry, TemplateDir):
            materialize(entry, path)
        else:
            with open(path / entry.name, "x") as f:
                f.write(entry.content)
    return path
