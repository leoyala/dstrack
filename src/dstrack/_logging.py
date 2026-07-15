"""Logging setup for dstrack.

Every module gets its own logger via ``logging.getLogger(__name__)``, all of
which are children of, and propagate to, the top-level ``"dstrack"`` logger
configured here. Applications embedding dstrack are free to configure that
hierarchy however they like: ``logging.basicConfig()``, attaching handlers to
the root logger, or attaching handlers directly to ``logging.getLogger("dstrack")``.

If the application does none of that,
[_DefaultHandler][dstrack._logging._DefaultHandler] prints
``WARNING``-and-above records to stderr with a sensible default format so
messages are never silently dropped. It steps aside automatically the moment
it detects that the application has configured logging itself.
"""

import logging
from typing import TextIO

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

logger = logging.getLogger("dstrack")


class _DefaultHandler(logging.StreamHandler[TextIO]):
    """Stderr handler used only until the application configures logging.

    Yields to any handler the application attaches to the root logger or to
    the ``dstrack`` logger, so it never produces duplicate output.
    """

    def emit(self, record: logging.LogRecord) -> None:
        if logging.root.handlers:
            return
        if any(h is not self for h in logger.handlers):
            return
        super().emit(record)


def _configure_default_logging() -> None:
    """Attach the fallback stderr handler to the ``dstrack`` logger.

    Called once, at import time. The handler installed here checks, on every
    record, whether the application has since configured logging itself and
    stays silent if so, see
    [_DefaultHandler.emit()][dstrack._logging._DefaultHandler.emit].

    A no-op if a [_DefaultHandler][dstrack._logging._DefaultHandler] is already
    attached: since two of them would each treat the other as an
    application-supplied handler and both step aside, re-running this (e.g. via
    ``importlib.reload`` under Jupyter's autoreload) would otherwise silence
    dstrack's logging entirely.
    """
    if any(isinstance(h, _DefaultHandler) for h in logger.handlers):
        return
    handler = _DefaultHandler()
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)


_configure_default_logging()
