"""Reduces a ``cProfile`` run to a call tree of dstrack's own methods.

This module is pure model: it turns raw ``pstats`` frames into
[CallNode][dstrack._benchmark._profiling.CallNode] trees and knows nothing about
how they are displayed. Rendering lives in [dstrack._benchmark._report][].
"""

import cProfile
import pstats
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias

# (filename, lineno, funcname) — the key pstats uses to identify a profiled frame.
FrameKey: TypeAlias = tuple[str, int, str]
# (primitive_calls, total_calls, total_time, cumulative_time)
_FrameTotals: TypeAlias = tuple[int, int, float, float]
# What pstats.Stats.stats maps each frame to: its totals plus its callers.
_RawStats: TypeAlias = dict[
    FrameKey, tuple[int, int, float, float, dict[FrameKey, _FrameTotals]]
]


@dataclass(frozen=True)
class CallScope:
    """The source files whose profiled frames are worth reporting.

    A frame is in scope when it lives under ``root`` and under none of the
    ``exclude`` paths, which keeps the tree focused on the code being measured
    instead of the interpreter and the benchmark harness itself.

    Attributes:
        root: Directory every reported frame must live under.
        exclude: Sub-directories of ``root`` to leave out.
    """

    root: Path
    exclude: tuple[Path, ...] = ()

    def contains(self, filename: str) -> bool:
        """True if frames from ``filename`` belong in the call tree."""
        try:
            resolved = Path(filename).resolve()
        except OSError:
            return False
        if not resolved.is_relative_to(self.root):
            return False
        return not any(resolved.is_relative_to(path) for path in self.exclude)

    def location(self, filename: str, lineno: int) -> str:
        """Render a frame's source location relative to ``root``."""
        return f"{Path(filename).resolve().relative_to(self.root)}:{lineno}"


def dstrack_scope() -> CallScope:
    """Scope covering dstrack's own code, minus this benchmark package."""
    benchmark_package = Path(__file__).resolve().parent
    return CallScope(root=benchmark_package.parent, exclude=(benchmark_package,))


@dataclass(frozen=True)
class CallNode:
    """One method in the call tree, with the callees it was measured calling.

    Attributes:
        funcname: Name of the profiled function.
        location: Source location, relative to the scope root.
        num_calls: Total number of calls, including recursive ones.
        total_seconds: Time spent in the function itself.
        cumulative_seconds: Time spent in the function and everything it called.
        children: Callees, ordered by cumulative time, descending.
        hidden_children: Callees omitted to respect the tree's child limit.
        recursive: True if this node already appears higher up the same path,
            in which case its callees are not expanded again.
    """

    funcname: str
    location: str
    num_calls: int
    total_seconds: float
    cumulative_seconds: float
    children: tuple["CallNode", ...] = ()
    hidden_children: int = 0
    recursive: bool = False


@dataclass
class CallGraph:
    """Caller/callee edges among the profiled frames that fall within a scope.

    ``pstats`` records, for every frame, the frames that called it.  This
    inverts those edges so a method that calls other in-scope methods (e.g.
    [StatsComputer.compute()][dstrack.snapshot._stats.StatsComputer.compute]
    calling ``_build_dataset_stats``) becomes their parent, then projects the
    result into trees rooted at the methods nothing in scope called.
    """

    _scope: CallScope
    _totals: dict[FrameKey, _FrameTotals] = field(default_factory=dict)
    _children: dict[FrameKey, list[FrameKey]] = field(default_factory=dict)
    _roots: list[FrameKey] = field(default_factory=list)

    @classmethod
    def from_profile(cls, profiler: cProfile.Profile, scope: CallScope) -> "CallGraph":
        """Build the in-scope call graph of a profiler that has finished running."""
        raw: _RawStats = pstats.Stats(profiler).stats  # type: ignore[attr-defined]
        graph = cls(_scope=scope)
        frames = {key for key in raw if scope.contains(key[0])}
        callees: set[FrameKey] = set()
        entrypoints: set[FrameKey] = set()

        for key in frames:
            primitive_calls, num_calls, total_time, cumulative_time, callers = raw[key]
            graph._totals[key] = (
                primitive_calls,
                num_calls,
                total_time,
                cumulative_time,
            )
            for caller in callers:
                if caller in frames:
                    graph._children.setdefault(caller, []).append(key)
                    callees.add(key)
                else:
                    entrypoints.add(key)

        # A frame is a root if nothing in scope called it, or if it was also
        # entered from outside the scope.  The latter keeps external entry
        # points into a recursive component (f -> g -> f) as roots, which
        # ``frames - callees`` alone would drop, leaving the tree empty.
        graph._roots = graph._by_cumulative((frames - callees) | entrypoints)
        return graph

    @property
    def is_empty(self) -> bool:
        """True if the profiler captured no in-scope frames at all."""
        return not self._totals

    def trees(self, *, max_children: int) -> list[CallNode]:
        """Project the graph into call trees, hottest root first.

        Args:
            max_children: Maximum callees expanded under each node, ranked by
                cumulative time.  The rest are counted in ``hidden_children``.

        Raises:
            ValueError: If ``max_children`` is negative, which would slice the
                callee list from the end rather than cap it as documented.
        """
        if max_children < 0:
            raise ValueError(f"max_children must be >= 0, got {max_children}")
        return [
            self._expand(root, ancestors=frozenset({root}), max_children=max_children)
            for root in self._roots
        ]

    def _by_cumulative(self, keys: Iterable[FrameKey]) -> list[FrameKey]:
        return sorted(keys, key=lambda key: self._totals[key][3], reverse=True)

    def _expand(
        self,
        key: FrameKey,
        *,
        ancestors: frozenset[FrameKey],
        max_children: int,
    ) -> CallNode:
        """Build the node for ``key``, expanding its hottest callees.

        ``ancestors`` guards against (mutual) recursion: a callee already on the
        current path is emitted as a leaf flagged ``recursive`` rather than
        expanded again, which would otherwise never terminate.
        """
        callees = self._by_cumulative(self._children.get(key, []))
        shown, hidden = callees[:max_children], callees[max_children:]
        children = tuple(
            self._node(child, recursive=True)
            if child in ancestors
            else self._expand(
                child,
                ancestors=ancestors | {child},
                max_children=max_children,
            )
            for child in shown
        )
        return self._node(key, children=children, hidden_children=len(hidden))

    def _node(
        self,
        key: FrameKey,
        *,
        children: tuple[CallNode, ...] = (),
        hidden_children: int = 0,
        recursive: bool = False,
    ) -> CallNode:
        filename, lineno, funcname = key
        _primitive_calls, num_calls, total_time, cumulative_time = self._totals[key]
        return CallNode(
            funcname=funcname,
            location=self._scope.location(filename, lineno),
            num_calls=num_calls,
            total_seconds=total_time,
            cumulative_seconds=cumulative_time,
            children=children,
            hidden_children=hidden_children,
            recursive=recursive,
        )
