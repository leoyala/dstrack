"""Smoke test, run against the built wheel/sdist, not the source tree.

Usage (matches the release workflow):
  uv run --isolated --no-project --with dist/*.whl tests/smoke_test.py
"""

import sys


def test_import() -> None:
    """Check that dstrack is importable."""
    import dstrack  # noqa: F401


def test_version() -> None:
    """check that package version is accessible from imported package."""
    import dstrack

    assert isinstance(dstrack.__version__, str) and dstrack.__version__, (
        "__version__ must be a non-empty string"
    )


def test_cli_entry_point() -> None:
    """Verifies the CLI app is importable and runs."""
    from typer.testing import CliRunner

    from dstrack._cli import app

    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0, result.output


if __name__ == "__main__":
    tests = [test_import, test_version, test_cli_entry_point]
    failed = []
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except Exception as exc:
            print(f"FAIL  {t.__name__}: {exc}")
            failed.append(t.__name__)

    if failed:
        print(f"\n{len(failed)} test(s) failed.")
        sys.exit(1)

    print(f"\nAll {len(tests)} smoke tests passed.")
