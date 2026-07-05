"""Rich-based console output helpers for dstrack's CLI.

Gives every command a consistent way to report results to the user,
independent of the logging configuration.
"""

from rich.console import Console

console = Console()


def success(message: str) -> None:
    """Print a success message prefixed with a check mark."""
    console.print(f"[bold green]✔[/bold green] {message}")


def warning(message: str) -> None:
    """Print a warning message prefixed with a lightning bolt."""
    console.print(f"[bold yellow]⚡[/bold yellow] {message}")


def error(message: str) -> None:
    """Print an error message prefixed with a bug icon."""
    console.print(f"[bold red]\U0001f41b[/bold red] {message}")


def info(message: str) -> None:
    """Print general information with an info icon."""
    console.print(f"[bold blue]\N{INFORMATION SOURCE}[/bold blue] {message}")
