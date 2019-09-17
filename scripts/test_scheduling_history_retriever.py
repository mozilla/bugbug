# -*- coding: utf-8 -*-

import argparse
import os
import subprocess
import tarfile
from logging import INFO, basicConfig, getLogger

import requests

from bugbug.utils import download_check_etag, zstd_compress

basicConfig(level=INFO)
logger = getLogger(__name__)


URL = "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_test_scheduling_history.latest/artifacts/public/adr_cache.tar.xz"


class Retriever(object):
    def retrieve_test_scheduling_history(self):
        os.makedirs("/data", exist_ok=True)

        # Download previous cache.
        cache_path = os.path.abspath("/data/adr_cache")
        if not os.path.exists(cache_path):
            try:
                download_check_etag(URL, "/data/adr_cache.tar.xz")
                with tarfile.open("/data/adr_cache.tar.xz", "r:xz") as tar:
                    tar.extractall("/")
                assert os.path.exists(
                    "/data/adr_cache"
                ), "Decompressed adr cache exists"
            except requests.exceptions.HTTPError:
                logger.info("The adr cache is not available yet")

        # Setup adr cache configuration.
        os.makedirs(os.path.expanduser("~/.config/adr"), exist_ok=True)
        with open(os.path.expanduser("~/.config/adr/config.toml"), "w") as f:
            f.write(
                f"""[adr.cache.stores]
file = {{ driver = "file", path = "{cache_path}" }}
            """
            )

        # TODO: Increase timespan when https://github.com/ahal/ci-recipes/issues/6 is fixed.
        subprocess.run(
            [
                "run-adr",
                "ahal/ci-recipes",
                "recipe",
                "-o",
                os.path.abspath("/data/test_scheduling_history.json"),
                "-f",
                "json",
                "push_data",
                "--",
                "--from",
                "today-3month",
                "--to",
                "today-2day",
                "--branch",
                "autoland",
            ],
            check=True,
            stdout=subprocess.DEVNULL,  # Redirect to /dev/null, as the logs are too big otherwise.
        )

        zstd_compress("/data/test_scheduling_history.json")

        with tarfile.open("/data/adr_cache.tar.xz", "w:xz") as tar:
            tar.add("/data/adr_cache")


def main():
    description = "Retrieve and extract the test scheduling history from ActiveData"
    parser = argparse.ArgumentParser(description=description)

    # Parse args to show the help if `--help` is passed
    parser.parse_args()

    retriever = Retriever()
    retriever.retrieve_test_scheduling_history()


if __name__ == "__main__":
    main()
