class StoreInitError(Exception):
    """Raised when local store initialization fails partway through."""


class StoreNotFoundError(Exception):
    """Raised when no local store root can be resolved."""


class StoreCorruptionError(Exception):
    """Raised when a file in the local store cannot be read back as written."""


class DatasetNotFoundError(Exception):
    """Raised when a named dataset does not exist in the local store."""


class InputTooLargeError(Exception):
    """Raised when a dataset exceeds the configured snapshot row limit."""


class IndexUnusable(Exception):
    """The index exists but cannot be queried, and must be rebuilt."""
