from pathlib import Path


def get_invocation_path() -> Path:
    """Return the directory the user was in when invoking the CLI."""
    return Path.cwd()
