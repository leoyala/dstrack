class StoreInitError(Exception):
    """Raised when local store initialization fails partway through."""


class StoreNotFoundError(Exception):
    """Raised when no local store root can be resolved."""
