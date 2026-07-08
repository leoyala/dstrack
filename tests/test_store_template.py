"""Tests for the local store template in src/dstrack/_store_template.py."""

from pathlib import Path

import pytest

from dstrack._store_template import (
    STORE_TEMPLATE,
    TemplateDir,
    TemplateFile,
    materialize,
)
from dstrack.paths import STORE_DIRNAME

# ---------------------------------------------------------------------------
# STORE_TEMPLATE
# ---------------------------------------------------------------------------


def test_store_template_name_matches_store_dirname() -> None:
    """The template's root directory name matches the canonical store dirname."""
    assert STORE_TEMPLATE.name == STORE_DIRNAME


# ---------------------------------------------------------------------------
# materialize
# ---------------------------------------------------------------------------


def test_materialize_creates_expected_structure(tmp_path: Path) -> None:
    """Builds `.dstrack/`, `.dstrack/datasets/`, and `.dstrack/.gitignore`."""
    store_path = materialize(STORE_TEMPLATE, tmp_path)

    assert store_path == tmp_path / STORE_DIRNAME
    assert store_path.is_dir()
    assert (store_path / "datasets").is_dir()
    assert (store_path / ".gitignore").is_file()
    assert (store_path / ".gitignore").read_text() == ".cache/"


def test_materialize_raises_if_target_already_exists(tmp_path: Path) -> None:
    """Refuses to overwrite an already-existing directory."""
    (tmp_path / STORE_DIRNAME).mkdir()

    with pytest.raises(FileExistsError):
        materialize(STORE_TEMPLATE, tmp_path)


def test_materialize_handles_nested_directories(tmp_path: Path) -> None:
    """Recurses into nested `TemplateDir` entries, not just top-level ones."""
    template = TemplateDir(
        name="root",
        entries=(
            TemplateDir(
                name="child",
                entries=(TemplateFile(name="leaf.txt", content="hello"),),
            ),
        ),
    )

    root_path = materialize(template, tmp_path)

    leaf_path = root_path / "child" / "leaf.txt"
    assert leaf_path.is_file()
    assert leaf_path.read_text() == "hello"
