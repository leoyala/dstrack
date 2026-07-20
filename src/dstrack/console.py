"""Rich-based console output helpers for dstrack's CLI.

Gives every command a consistent way to report results to the user,
independent of the logging configuration.

Messages are plain text, not rich markup. What they report is routinely
user-derived -- a dataset name from `dstrack track --name`, a path, an
exception carrying either -- and square brackets are legal in all of them.
Rendering such a message as markup would swallow ``[bold]`` as styling and
raise outright on ``[/]``, so each helper escapes its message and applies
styling only to the icon it puts in front.
"""

from rich.console import Console, RenderableType
from rich.markup import escape

console = Console()


def display(renderable: RenderableType) -> None:
    """Print a rich renderable, e.g. a table or a timeline.

    For command output that is built rather than phrased: unlike the message
    helpers below, what is passed is rendered as-is, so a caller assembling
    text from user-derived values should build it with `rich.text.Text`.
    """
    console.print(renderable)


def success(message: str) -> None:
    """Print a success message prefixed with a check mark."""
    console.print(f"[bold green]\N{HEAVY CHECK MARK}[/bold green] {escape(message)}")


def warning(message: str) -> None:
    """Print a warning message prefixed with a lightning bolt."""
    console.print(f"[bold yellow]\N{HIGH VOLTAGE SIGN}[/bold yellow] {escape(message)}")


def error(message: str) -> None:
    """Print an error message prefixed with a bug icon."""
    console.print(f"[bold red]\N{BUG}[/bold red] {escape(message)}")


def info(message: str) -> None:
    """Print general information with an info icon."""
    console.print(f"[bold blue]\N{INFORMATION SOURCE}[/bold blue] {escape(message)}")
