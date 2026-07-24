"""Logging configuration, applied on import.

Import this before anything that pulls in mozci. mozci logs through loguru,
which writes straight to stderr in its own format and ignores stdlib logging
levels, and it emits lines while being imported -- so configuring later would
both miss those lines and leave its per-task chatter unfiltered.
"""

import logging

from app.config import settings


def _forward_to_stdlib(message) -> None:
    """Re-emit a loguru record through stdlib logging (levels are numbered alike)."""
    record = message.record
    logging.getLogger(record["name"] or "mozci").log(
        record["level"].no, record["message"]
    )


def configure() -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # httpx logs every request at INFO, which drowns out our own lines.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _capture_loguru()


def _capture_loguru() -> None:
    """Route mozci's loguru output into stdlib logging at ``mozci_log_level``.

    mozci warns per task about groups missing from a task's errorsummary, dozens
    of times a minute; that is inherent to how it reads CI data and is not
    actionable here. Importing ``mozci`` runs its own ``setup_logging()``, which
    removes every loguru sink and installs its own on stderr, so the import is
    bracketed: drop the default sink first (silencing what mozci logs while being
    imported), then take the sink back from it.
    """
    try:
        from loguru import logger as loguru_logger
    except ImportError:
        return

    loguru_logger.remove()
    try:
        import mozci  # noqa: F401
    except ImportError:
        pass
    loguru_logger.remove()
    loguru_logger.add(_forward_to_stdlib, level=settings.mozci_log_level.upper())


configure()
