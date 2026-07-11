"""Renders benchmark runs to a Rich console.

This is the only layer that knows what a benchmark looks like on screen.  It
implements :class:`~dstrack._benchmark._runner.BenchmarkObserver`, so the same
object reports progress during a run and the tables after it.
"""

from pathlib import Path

from rich.console import Console
from rich.table import Table

from dstrack._benchmark._environment import EnvironmentInfo
from dstrack._benchmark._profiling import CallGraph, CallNode
from dstrack._benchmark._runner import BenchmarkResult, BenchmarkRun
from dstrack.console import console as default_console

_LAST_CONNECTOR = "└── "
_MID_CONNECTOR = "├── "
_LAST_GUIDE = "    "
_MID_GUIDE = "│   "


class ConsoleReporter:
    """Prints a benchmark's progress and results as Rich tables.

    Args:
        console: Destination console.  Defaults to dstrack's shared console;
            inject another to capture the output in tests.
    """

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or default_console

    def generating_csv(self, path: Path, num_rows: int) -> None:
        self._console.print(
            f"Generating synthetic CSV at {path} ({num_rows:,} rows)..."
        )

    def building_metadata(self) -> None:
        self._console.print("Building snapshot metadata...")

    def computing_stats(self) -> None:
        self._console.print("Computing snapshot statistics...")

    def report(self, run: BenchmarkRun, *, profile_limit: int) -> None:
        """Print the environment, the timings, and the profile call tree.

        Args:
            run: The finished benchmark run.
            profile_limit: Max callees shown under each node of the call tree.
        """
        self._console.print(_environment_table(run.environment))
        self._console.print(_results_table(run.result))
        if run.call_graph is not None:
            self._console.print(_call_tree_table(run.call_graph, profile_limit))

    def csv_kept(self, path: Path) -> None:
        """Report that the generated CSV was left on disk."""
        self._console.print(f"Synthetic CSV kept at {path}")


def _environment_table(env: EnvironmentInfo) -> Table:
    table = Table(title="Environment")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("OS", f"{env.os_name} {env.os_release}")
    table.add_row("OS version", env.os_version)
    table.add_row("Machine", env.machine)
    table.add_row("CPU model", env.cpu_model)
    table.add_row("CPU count", str(env.cpu_count) if env.cpu_count else "unknown")
    table.add_row(
        "Total memory",
        f"{env.total_memory_gb:.2f} GB"
        if env.total_memory_gb is not None
        else "unknown",
    )
    table.add_row("Python", f"{env.python_implementation} {env.python_version}")
    table.add_row("dstrack version", env.dstrack_version)
    return table


def _results_table(result: BenchmarkResult) -> Table:
    table = Table(title="Snapshot benchmark")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Rows generated", f"{result.num_rows:,}")
    table.add_row("Columns", str(result.num_columns))
    table.add_row("CSV size", f"{result.csv_size_bytes / (1024**2):.2f} MB")
    table.add_row("CSV generation time", f"{result.generation_seconds:.3f} s")
    table.add_row("Metadata build time", f"{result.metadata_seconds:.3f} s")
    table.add_row("Stats compute time", f"{result.stats_seconds:.3f} s")
    table.add_row("Total snapshot time", f"{result.total_snapshot_seconds:.3f} s")
    table.add_row("Throughput", f"{result.rows_per_second:,.0f} rows/s")
    table.add_row("schema_hash", result.schema_hash)
    return table


def _call_tree_table(graph: CallGraph, profile_limit: int) -> Table:
    """Render dstrack's call tree, hottest root first.

    Guide characters (``├──``, ``└──``, ``│``) are baked into the "Method" cell
    as plain text so the Calls/Total/Cumulative columns line up as a normal
    table regardless of tree depth.
    """
    table = Table(title="dstrack call tree (by cumulative time)")
    table.add_column("Method", overflow="fold", ratio=1)
    table.add_column("Calls", justify="right", no_wrap=True)
    table.add_column("Total (s)", justify="right", no_wrap=True)
    table.add_column("Cumulative (s)", justify="right", no_wrap=True)

    if graph.is_empty:
        table.add_row("(no dstrack frames captured)", "-", "-", "-")
        return table

    roots = graph.trees(max_children=profile_limit)
    for i, root in enumerate(roots):
        _add_node_rows(table, root, prefix="", is_last=i == len(roots) - 1)
    return table


def _add_node_rows(table: Table, node: CallNode, *, prefix: str, is_last: bool) -> None:
    """Add a row for ``node`` and, recursively, for the callees under it."""
    connector = _LAST_CONNECTOR if is_last else _MID_CONNECTOR
    table.add_row(
        f"{prefix}{connector}{_format_node(node)}",
        str(node.num_calls),
        f"{node.total_seconds:.4f}",
        f"{node.cumulative_seconds:.4f}",
    )

    child_prefix = prefix + (_LAST_GUIDE if is_last else _MID_GUIDE)
    for i, child in enumerate(node.children):
        child_is_last = i == len(node.children) - 1 and not node.hidden_children
        _add_node_rows(table, child, prefix=child_prefix, is_last=child_is_last)

    if node.hidden_children:
        table.add_row(
            f"{child_prefix}{_LAST_CONNECTOR}[dim]... {node.hidden_children} more "
            "(raise --profile-limit to see them)[/dim]",
            "-",
            "-",
            "-",
        )


def _format_node(node: CallNode) -> str:
    """Render a node's function name and source location."""
    label = f"[bold]{node.funcname}[/bold] [dim]{node.location}[/dim]"
    if node.recursive:
        label += " [dim italic](recursive, not expanded)[/dim italic]"
    return label
