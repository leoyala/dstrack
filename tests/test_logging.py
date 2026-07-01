import io
import logging

import pytest

import dstrack._logging as dstrack_logging
from dstrack._logging import _DefaultHandler, logger

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_record(level: int = logging.WARNING, msg: str = "hello") -> logging.LogRecord:
    """Build a bare LogRecord for exercising _DefaultHandler.emit directly."""
    return logging.LogRecord(
        name="dstrack",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


# ---------------------------------------------------------------------------
# Package wiring
# ---------------------------------------------------------------------------


def test_default_handler_attached_on_import() -> None:
    """Importing dstrack attaches exactly one _DefaultHandler to the dstrack logger."""
    default_handlers = [h for h in logger.handlers if isinstance(h, _DefaultHandler)]
    assert len(default_handlers) == 1


def test_configure_default_logging_is_idempotent() -> None:
    """Calling _configure_default_logging again does not attach a duplicate handler.

    Guards against the reload scenario (e.g. Jupyter's %autoreload) where two
    _DefaultHandler instances would each treat the other as an
    application-supplied handler and both step aside, silencing dstrack's
    logging entirely.
    """
    before = len(logger.handlers)
    dstrack_logging._configure_default_logging()
    assert len(logger.handlers) == before


# ---------------------------------------------------------------------------
# _DefaultHandler.emit
# ---------------------------------------------------------------------------


def test_emits_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no root handlers and no other dstrack handlers, records are written out."""
    handler = _DefaultHandler(io.StringIO())
    monkeypatch.setattr(logging.root, "handlers", [], raising=False)
    monkeypatch.setattr(logger, "handlers", [handler], raising=False)

    handler.emit(make_record(msg="uh oh"))

    assert "uh oh" in handler.stream.getvalue()


def test_yields_when_root_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the root logger has handlers (app called basicConfig etc.), stay silent."""
    handler = _DefaultHandler(io.StringIO())
    monkeypatch.setattr(
        logging.root, "handlers", [logging.NullHandler()], raising=False
    )
    monkeypatch.setattr(logger, "handlers", [handler], raising=False)

    handler.emit(make_record(msg="should not appear"))

    assert handler.stream.getvalue() == ""


def test_yields_when_dstrack_logger_has_other_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the app attached its own handler to the dstrack logger, stay silent."""
    handler = _DefaultHandler(io.StringIO())
    monkeypatch.setattr(logging.root, "handlers", [], raising=False)
    monkeypatch.setattr(
        logger, "handlers", [handler, logging.NullHandler()], raising=False
    )

    handler.emit(make_record(msg="should not appear"))

    assert handler.stream.getvalue() == ""
