"""Read which Firefox build the agent drives, to record it in the result."""

import configparser
import logging
from pathlib import Path

logger = logging.getLogger("autowebcompat-etp")


def firefox_build(binary: Path) -> tuple[str | None, str | None]:
    """The version and build ID of the Firefox at `binary`, from application.ini.

    (None, None) if the file isn't there or can't be read.
    """
    for ini in (
        binary.parent / "application.ini",
        # macOS: Nightly.app/Contents/MacOS/firefox.
        binary.parent.parent / "Resources" / "application.ini",
    ):
        parser = configparser.ConfigParser()
        try:
            if not parser.read(ini):
                continue
            return parser.get("App", "Version"), parser.get("App", "BuildID")
        except configparser.Error, OSError:
            logger.exception("could not read %s", ini)
    return None, None
