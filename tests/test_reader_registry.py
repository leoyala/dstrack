"""Tests for reader registration and resolution.

Covers `dstrack.readers._registry` and `dstrack.readers._resolve`: the three ways
a reader is reached by name (extension inference, short name, import spec), and
the protocol checks that must happen *before* a resolved class is instantiated.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from dstrack.readers import (
    Cell,
    ColumnInfo,
    CsvReader,
    ReaderFactory,
    TabularReader,
    _registry,
    available_readers,
    known_extensions,
    load_reader_class,
    register_reader,
    resolve_reader,
    resolve_reader_class,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Set by GoodReader.__init__/from_path so tests can assert whether a resolved
# class was constructed. A class that fails validation must never be.
CONSTRUCTED: list[Path] = []


class GoodReader:
    """A complete reader: satisfies TabularReader and ReaderFactory."""

    EXTENSIONS = (".good",)

    @classmethod
    def from_path(cls, path: Path) -> "GoodReader":
        return cls(path)

    def __init__(self, path: Path) -> None:
        CONSTRUCTED.append(Path(path))
        self._path = Path(path)

    def columns(self) -> list[ColumnInfo]:
        return [ColumnInfo("x", "int64")]

    def iter_batches(self, batch_size: int = 1000) -> Iterator[list[list[Cell]]]:
        return iter([[[1]]])


class NoFromPathReader:
    """Reads fine, but cannot be built from a path alone."""

    def __init__(self, path: Path) -> None:
        CONSTRUCTED.append(Path(path))

    def columns(self) -> list[ColumnInfo]:
        return []

    def iter_batches(self, batch_size: int = 1000) -> Iterator[list[list[Cell]]]:
        return iter([])


class NotAReader:
    """Has from_path, but none of the reading methods."""

    @classmethod
    def from_path(cls, path: Path) -> "NotAReader":
        CONSTRUCTED.append(Path(path))
        return cls()


class InstanceFromPathReader:
    """Reads fine and has from_path, but forgot the @classmethod decorator.

    Passes a bare ``isinstance(cls, ReaderFactory)`` check because the attribute
    exists, yet ``cls.from_path(path)`` would bind ``path`` to ``self``.
    """

    def from_path(self, path: Path) -> "InstanceFromPathReader":
        CONSTRUCTED.append(Path(path))
        return self

    def columns(self) -> list[ColumnInfo]:
        return []

    def iter_batches(self, batch_size: int = 1000) -> Iterator[list[list[Cell]]]:
        return iter([])


not_a_class = "definitely not a class"


class FakeEntryPoint:
    """Stand-in for importlib.metadata.EntryPoint."""

    def __init__(self, name: str, value: str, loaded: object) -> None:
        self.name = name
        self.value = value
        self._loaded = loaded

    def load(self) -> object:
        if isinstance(self._loaded, Exception):
            raise self._loaded
        return self._loaded


@pytest.fixture(autouse=True)
def isolate_registry() -> Iterator[None]:
    """Restore the process-wide registry after each test."""
    by_name = dict(_registry._BY_NAME)
    by_extension = dict(_registry._BY_EXTENSION)
    plugins_loaded = _registry._plugins_loaded
    CONSTRUCTED.clear()
    yield
    _registry._BY_NAME.clear()
    _registry._BY_NAME.update(by_name)
    _registry._BY_EXTENSION.clear()
    _registry._BY_EXTENSION.update(by_extension)
    _registry._plugins_loaded = plugins_loaded


def spec_for(cls: type) -> str:
    """The 'module:ClassName' spec that resolves back to *cls*."""
    return f"{cls.__module__}:{cls.__qualname__}"


# ---------------------------------------------------------------------------
# Built-in registration
# ---------------------------------------------------------------------------


def test_csv_reader_registered_by_name_and_extension() -> None:
    """The built-in CsvReader is registered out of the box, both ways."""
    assert available_readers()["csv"] is CsvReader
    assert known_extensions()[".csv"] is CsvReader


def test_csv_reader_satisfies_reader_factory() -> None:
    """ReaderFactory is checked against the class object, not an instance."""
    assert isinstance(CsvReader, ReaderFactory)


def test_csv_from_path_builds_a_reader(tmp_path: Path) -> None:
    """from_path() constructs a working reader with default options."""
    path = tmp_path / "a.csv"
    path.write_text("x\n1\n", encoding="utf-8")

    reader = CsvReader.from_path(path)

    assert isinstance(reader, TabularReader)
    assert [c.name for c in reader.columns()] == ["x"]


# ---------------------------------------------------------------------------
# Resolution: extension, name, spec
# ---------------------------------------------------------------------------


def test_resolves_from_extension(tmp_path: Path) -> None:
    """With no reader named, the extension picks one."""
    path = tmp_path / "a.csv"
    path.write_text("x\n1\n", encoding="utf-8")

    assert isinstance(resolve_reader(path), CsvReader)


def test_resolves_from_short_name(tmp_path: Path) -> None:
    """A registered name wins over the extension, which may be absent or wrong."""
    path = tmp_path / "export.data"
    path.write_text("x\n1\n", encoding="utf-8")

    assert isinstance(resolve_reader(path, reader="csv"), CsvReader)


def test_resolves_from_import_spec(tmp_path: Path) -> None:
    """A 'module:ClassName' spec imports a reader that is not registered at all."""
    path = tmp_path / "a.thing"
    path.touch()

    reader = resolve_reader(path, reader=spec_for(GoodReader))

    assert isinstance(reader, GoodReader)
    assert [path] == CONSTRUCTED


def test_spec_is_told_apart_from_name_by_the_colon(tmp_path: Path) -> None:
    """A value containing ':' is a spec; one without it is a registered name."""
    path = tmp_path / "a.thing"
    path.touch()

    assert resolve_reader_class(path, reader=spec_for(GoodReader)) is GoodReader

    register_reader(GoodReader, name="good")
    assert resolve_reader_class(path, reader="good") is GoodReader


def test_extension_inference_finds_a_registered_reader(tmp_path: Path) -> None:
    """Registering a reader makes its extensions resolve with nothing typed.

    This is the case that --reader used to have to cover on every invocation.
    """
    register_reader(GoodReader, name="good")
    path = tmp_path / "data.good"
    path.touch()

    assert isinstance(resolve_reader(path), GoodReader)


# ---------------------------------------------------------------------------
# Resolution failures
# ---------------------------------------------------------------------------


def test_unknown_extension_lists_the_known_ones(tmp_path: Path) -> None:
    """An unclaimed extension names the ones that would have worked."""
    path = tmp_path / "data.parquet"
    path.touch()

    with pytest.raises(ValueError, match=r"No reader registered for extension"):
        resolve_reader(path)


def test_unknown_name_lists_available_readers(tmp_path: Path) -> None:
    """A --reader typo names the readers that are actually registered."""
    path = tmp_path / "a.csv"
    path.touch()

    with pytest.raises(ValueError, match=r"Unknown reader 'parquet'.*csv"):
        resolve_reader(path, reader="parquet")


@pytest.mark.parametrize("spec", ["nocolon", ":Class", "module:", ""])
def test_malformed_spec_raises_value_error(spec: str) -> None:
    """A spec missing either half of 'module:ClassName' is rejected as a spec."""
    with pytest.raises(ValueError, match="Invalid reader spec"):
        load_reader_class(spec)


def test_unimportable_module_raises_value_error() -> None:
    """A spec naming a package that isn't installed fails on the import, not later."""
    with pytest.raises(ValueError, match="Could not import module"):
        load_reader_class("dstrack_no_such_package:Reader")


def test_missing_class_raises_value_error() -> None:
    """A spec whose module imports but has no such class says so."""
    with pytest.raises(ValueError, match="has no attribute"):
        load_reader_class("dstrack.readers:NoSuchReader")


# ---------------------------------------------------------------------------
# Validation happens before instantiation
# ---------------------------------------------------------------------------


def test_class_missing_read_methods_is_rejected_unconstructed(tmp_path: Path) -> None:
    """A class that isn't a TabularReader is rejected before it is ever called."""
    path = tmp_path / "a.thing"
    path.touch()

    with pytest.raises(TypeError, match="TabularReader"):
        resolve_reader(path, reader=spec_for(NotAReader))

    assert CONSTRUCTED == []


def test_class_missing_from_path_is_rejected_unconstructed(tmp_path: Path) -> None:
    """Reading methods alone are not enough to be reachable by name."""
    path = tmp_path / "a.thing"
    path.touch()

    with pytest.raises(TypeError, match="ReaderFactory"):
        resolve_reader(path, reader=spec_for(NoFromPathReader))

    assert CONSTRUCTED == []


def test_instance_method_from_path_is_rejected_unconstructed(tmp_path: Path) -> None:
    """A from_path that forgot @classmethod is caught before it is ever called."""
    path = tmp_path / "a.thing"
    path.touch()

    with pytest.raises(TypeError, match="classmethod"):
        resolve_reader(path, reader=spec_for(InstanceFromPathReader))

    assert CONSTRUCTED == []


def test_register_reader_rejects_instance_method_from_path() -> None:
    """The non-classmethod check also guards registration, not just build time."""
    with pytest.raises(TypeError, match="classmethod"):
        register_reader(InstanceFromPathReader, name="bad")  # type: ignore[arg-type]

    assert "bad" not in available_readers()


def test_build_reader_rejects_non_reader_return(tmp_path: Path) -> None:
    """build_reader rejects a from_path that returns something un-readable."""
    path = tmp_path / "a.thing"
    path.touch()

    class BadReturnReader:
        @classmethod
        def from_path(cls, path: Path) -> object:
            return "not a reader"

        def columns(self) -> list[ColumnInfo]:
            return []

        def iter_batches(self, batch_size: int = 1000) -> Iterator[list[list[Cell]]]:
            return iter([])

    with pytest.raises(TypeError, match="not a TabularReader"):
        _registry.build_reader(BadReturnReader, path)


def test_non_class_target_is_rejected() -> None:
    """A spec that names a module-level value, not a class, fails cleanly."""
    with pytest.raises(TypeError, match="not a class"):
        load_reader_class(spec_for.__module__ + ":not_a_class")


def test_register_reader_rejects_invalid_class() -> None:
    """A class that fails validation leaves no half-finished registration behind."""
    with pytest.raises(TypeError, match="ReaderFactory"):
        register_reader(NoFromPathReader, name="bad")  # type: ignore[arg-type]

    assert "bad" not in available_readers()


# ---------------------------------------------------------------------------
# Registration conflicts
# ---------------------------------------------------------------------------


def test_duplicate_name_raises() -> None:
    """Registration never silently displaces an existing reader."""
    with pytest.raises(ValueError, match="already registered"):
        register_reader(GoodReader, name="csv", extensions=())


def test_duplicate_extension_raises() -> None:
    """Two readers fighting over one extension is a conflict the user has to see."""
    with pytest.raises(ValueError, match=r"Extension '.csv' is already registered"):
        register_reader(GoodReader, name="good", extensions=[".csv"])


@pytest.mark.parametrize("ext", ["csv", "", ".", ".tar.gz"])
def test_malformed_extension_raises(ext: str) -> None:
    """An extension that could never match Path.suffix is rejected, not stored."""
    with pytest.raises(ValueError, match="malformed"):
        register_reader(GoodReader, name="good", extensions=[ext])

    assert ext not in known_extensions()
    assert "good" not in available_readers()


def test_malformed_extension_leaves_registry_untouched() -> None:
    """A bad extension is caught before the name or any extension is claimed."""
    with pytest.raises(ValueError, match="malformed"):
        register_reader(GoodReader, name="good", extensions=[".fine", "bad"])

    assert "good" not in available_readers()
    assert ".fine" not in known_extensions()


def test_explicit_extensions_override_the_class_attribute() -> None:
    """Passing extensions replaces EXTENSIONS rather than adding to it."""
    register_reader(GoodReader, name="good", extensions=[".other"])

    assert ".other" in known_extensions()
    assert ".good" not in known_extensions()


# ---------------------------------------------------------------------------
# Plugin discovery
# ---------------------------------------------------------------------------


def test_entry_point_plugin_is_registered_lazily(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An installed plugin claims its extensions with nothing typed by the user."""
    monkeypatch.setattr(
        _registry,
        "entry_points",
        lambda group: [FakeEntryPoint("good", spec_for(GoodReader), GoodReader)],
    )
    _registry._plugins_loaded = False

    path = tmp_path / "data.good"
    path.touch()

    assert isinstance(resolve_reader(path), GoodReader)
    assert available_readers()["good"] is GoodReader


def test_broken_plugin_is_skipped_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One broken plugin must not break tracking a dataset that doesn't use it."""
    monkeypatch.setattr(
        _registry,
        "entry_points",
        lambda group: [
            FakeEntryPoint("boom", "broken:Reader", ImportError("no such module")),
            FakeEntryPoint("alsobad", spec_for(NotAReader), NotAReader),
        ],
    )
    _registry._plugins_loaded = False

    path = tmp_path / "a.csv"
    path.write_text("x\n1\n", encoding="utf-8")

    assert isinstance(resolve_reader(path), CsvReader)
    assert "boom" not in available_readers()
    assert "alsobad" not in available_readers()


def test_plugins_are_loaded_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing plugin is not retried on every lookup."""
    calls = []

    def fake_entry_points(group: str) -> list[FakeEntryPoint]:
        calls.append(group)
        return []

    monkeypatch.setattr(_registry, "entry_points", fake_entry_points)
    _registry._plugins_loaded = False

    available_readers()
    available_readers()
    known_extensions()

    assert calls == ["dstrack.readers"]
