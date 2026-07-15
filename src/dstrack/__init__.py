from importlib.metadata import PackageNotFoundError, version

from . import _logging as _logging

try:
    __version__ = version("dstrack")
except PackageNotFoundError:
    __version__ = "unknown"
