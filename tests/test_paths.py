"""Tests for path resolution."""

import os
from pathlib import Path

import pytest

from dstrack import paths
from dstrack.errors import StoreNotFoundError
from dstrack.paths import ROOT_PATH_ENV_VAR, resolve_store_root

# ---------------------------------------------------------------------------
# resolve_store_root
# ---------------------------------------------------------------------------


def test_resolve_store_root_uses_root_when_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit `cli_root` wins, regardless of env var or cwd."""
    monkeypatch.setenv(ROOT_PATH_ENV_VAR, str(tmp_path / "env-root"))
    monkeypatch.chdir(tmp_path)

    root = tmp_path / "cli-root"

    assert resolve_store_root(root=root) == root.resolve() / paths.STORE_DIRNAME


def test_resolve_store_root_uses_env_var_when_no_cli_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The env var is used when no `cli_root` is given."""
    env_root = tmp_path / "env-root"
    monkeypatch.setenv(ROOT_PATH_ENV_VAR, str(env_root))
    monkeypatch.chdir(tmp_path)

    assert resolve_store_root() == env_root.resolve() / paths.STORE_DIRNAME


def test_resolve_store_root_finds_dstrack_in_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falls back to walking up from cwd, finding `STORE_DIRNAME/` right there."""
    monkeypatch.delenv(ROOT_PATH_ENV_VAR, raising=False)
    (tmp_path / paths.STORE_DIRNAME).mkdir()
    monkeypatch.chdir(tmp_path)

    assert resolve_store_root() == tmp_path.resolve() / paths.STORE_DIRNAME


def test_resolve_store_root_finds_dstrack_in_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Walks up through parent directories until `STORE_DIRNAME/` is found."""
    monkeypatch.delenv(ROOT_PATH_ENV_VAR, raising=False)
    (tmp_path / paths.STORE_DIRNAME).mkdir()
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    assert resolve_store_root() == tmp_path.resolve() / paths.STORE_DIRNAME


def test_resolve_store_root_raises_when_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raises `StoreNotFoundError` if no `STORE_DIRNAME/` exists anywhere above cwd."""
    monkeypatch.delenv(ROOT_PATH_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(StoreNotFoundError):
        resolve_store_root()


# ---------------------------------------------------------------------------
# _find_store_root
# ---------------------------------------------------------------------------


def test_find_store_root_error_message_mentions_overrides(
    tmp_path: Path,
) -> None:
    """Error message points users at both override mechanisms."""
    with pytest.raises(StoreNotFoundError) as exc_info:
        paths._find_store_root(tmp_path)

    assert "--root" in str(exc_info.value)
    assert ROOT_PATH_ENV_VAR in str(exc_info.value)


def test_find_store_root_stops_at_filesystem_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never searches past a directory that lives on a different device.

    Even if `STORE_DIRNAME/` exists further up, on the other side of the
    boundary, like git it must not be found.
    """
    (tmp_path / paths.STORE_DIRNAME).mkdir()
    nested = tmp_path / "mounted"
    nested.mkdir()

    real_stat = Path.stat

    class _FakeStat:
        """Wraps a real `stat_result`, overriding only `st_dev`."""

        def __init__(self, real_result: os.stat_result, st_dev: int) -> None:
            self._real_result = real_result
            self.st_dev = st_dev

        def __getattr__(self, name: str) -> object:
            return getattr(self._real_result, name)

    def fake_stat(self: Path, *args: object, **kwargs: object) -> _FakeStat:
        on_mount = self == nested or nested in self.parents
        real_result = real_stat(self, *args, **kwargs)
        return _FakeStat(real_result, st_dev=1 if on_mount else 2)

    monkeypatch.setattr(Path, "stat", fake_stat)

    with pytest.raises(StoreNotFoundError):
        paths._find_store_root(nested)
