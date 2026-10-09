import logging
import os
import subprocess
from typing import Self

logger = logging.getLogger(__name__)


class Environment:
    def __init__(self) -> None:
        self.started_processes: list[subprocess.Popen] = []

    def start(self, cmd: list[str]) -> None:
        logger.info("Running %s", " ".join(cmd))
        self.started_processes.append(subprocess.Popen(cmd))

    def start_xvfb(self) -> None:
        self.start(
            [
                "Xvfb",
                os.environ["DISPLAY"],
                "-screen",
                "0",
                "%sx%sx%s"
                % (
                    os.environ["SCREEN_WIDTH"],
                    os.environ["SCREEN_HEIGHT"],
                    os.environ["SCREEN_DEPTH"],
                ),
            ]
        )
        self.start(["fluxbox", "-display", os.environ["DISPLAY"]])

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args, **kwargs) -> None:
        for process in self.started_processes:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
