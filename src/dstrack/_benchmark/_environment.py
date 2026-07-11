"""Describes the machine and runtime a benchmark executed under.

Hardware facts have no portable API, so each is read by a per-OS probe.  Every
probe is best-effort: it returns ``None`` (or raises, which :func:`_attempt`
swallows) when the platform will not give up the answer, and the caller falls
back to something always available.
"""

import os
import platform
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from dstrack import __version__

T = TypeVar("T")

_PROBE_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class EnvironmentInfo:
    """Snapshot of the machine and runtime a benchmark executed under."""

    os_name: str
    os_release: str
    os_version: str
    machine: str
    cpu_model: str
    cpu_count: int | None
    total_memory_gb: float | None
    python_implementation: str
    python_version: str
    dstrack_version: str


def _attempt(probe: Callable[[], T | None]) -> T | None:
    """Run a probe, returning ``None`` if the platform refuses to answer."""
    try:
        return probe()
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _command_output(*args: str) -> str:
    """Gets the stdout of a command, raising if it fails or times out."""
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT_SECONDS,
        check=True,
    )
    return result.stdout.strip()


def _first_matching_line(path: Path, prefix: str) -> str | None:
    """First line of ``path`` starting with ``prefix`` (case-insensitive)."""
    if not path.is_file():
        return None
    for line in path.read_text().splitlines():
        if line.lower().startswith(prefix.lower()):
            return line
    return None


def _linux_cpu_model() -> str | None:
    line = _first_matching_line(Path("/proc/cpuinfo"), "model name")
    return line.split(":", 1)[1].strip() if line else None


def _darwin_cpu_model() -> str | None:
    return _command_output("sysctl", "-n", "machdep.cpu.brand_string")


def _windows_cpu_model() -> str | None:
    lines = [
        line.strip()
        for line in _command_output("wmic", "cpu", "get", "name").splitlines()
        if line.strip()
    ]
    return lines[1] if len(lines) >= 2 else None


def _linux_total_memory() -> int | None:
    line = _first_matching_line(Path("/proc/meminfo"), "MemTotal:")
    return int(line.split()[1]) * 1024 if line else None


def _darwin_total_memory() -> int | None:
    return int(_command_output("sysctl", "-n", "hw.memsize"))


def _sysconf_total_memory() -> int | None:
    """Page size times page count, on any POSIX platform exposing sysconf."""
    if not hasattr(os, "sysconf") or "SC_PAGE_SIZE" not in os.sysconf_names:
        return None
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")


_CPU_MODEL_PROBES: dict[str, Callable[[], str | None]] = {
    "Linux": _linux_cpu_model,
    "Darwin": _darwin_cpu_model,
    "Windows": _windows_cpu_model,
}

_TOTAL_MEMORY_PROBES: dict[str, Callable[[], int | None]] = {
    "Linux": _linux_total_memory,
    "Darwin": _darwin_total_memory,
}


def _cpu_model() -> str:
    """Best-effort human-readable CPU model string for the current machine."""
    probe = _CPU_MODEL_PROBES.get(platform.system())
    model = _attempt(probe) if probe is not None else None
    return model or platform.processor() or platform.machine() or "unknown"


def _total_memory_bytes() -> int | None:
    """Best-effort total physical memory in bytes, or None if it can't be read."""
    probe = _TOTAL_MEMORY_PROBES.get(platform.system(), _sysconf_total_memory)
    return _attempt(probe)


def collect_environment_info() -> EnvironmentInfo:
    """Collect OS, hardware, and runtime details describing the current machine."""
    total_memory = _total_memory_bytes()
    return EnvironmentInfo(
        os_name=platform.system(),
        os_release=platform.release(),
        os_version=platform.version(),
        machine=platform.machine(),
        cpu_model=_cpu_model(),
        cpu_count=os.cpu_count(),
        total_memory_gb=total_memory / (1024**3) if total_memory is not None else None,
        python_implementation=platform.python_implementation(),
        python_version=platform.python_version(),
        dstrack_version=__version__,
    )
