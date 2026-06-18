"""Smoke test, run against the built wheel/sdist, not the source tree.

Usage (matches the release workflow):
  uv run --isolated --no-project --with dist/*.whl tests/smoke_test.py
"""

import sys


def test_import() -> None:
    import dstrack  # noqa: F401


def test_version() -> None:
    import dstrack

    assert isinstance(dstrack.__version__, str) and dstrack.__version__, (
        "__version__ must be a non-empty string"
    )


def test_cli_entry_point() -> None:
    # Verifies the entry point function is importable and callable (the script
    # wiring in pyproject.toml points here).
    from dstrack import main

    main()


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
