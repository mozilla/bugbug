# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import subprocess
import time

import requests

from bugbug import utils

logger = logging.getLogger(__name__)


START_RETRIES = 14
HEADERS = {"Content-type": "application/octet-stream"}


class RustCodeAnalysisServer:
    def __init__(self):
        for _ in range(START_RETRIES):
            self.start_process()

            for _ in range(START_RETRIES):
                if self.ping():
                    logger.info("Rust code analysis server is ready to accept queries")
                    return
                else:
                    if self.proc.poll() is not None:
                        break

                    time.sleep(0.35)

        self.terminate()
        raise Exception("Unable to run rust-code-analysis server")

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    def start_process(self):
        self.port = utils.get_free_tcp_port()

        try:
            self.proc = subprocess.Popen(
                ["rust-code-analysis-web", "--port", str(self.port)]
            )
        except FileNotFoundError:
            raise Exception("rust-code-analysis is required for code analysis")

    def terminate(self):
        if self.proc is not None:
            self.proc.terminate()

    def __str__(self):
        return f"Server running at {self.base_url}"

    def ping(self):
        try:
            r = requests.get(f"{self.base_url}/ping")
            return r.ok
        except requests.exceptions.ConnectionError:
            return False

    def metrics(self, filename, code, unit=True):
        """
        When unit is True, then only metrics for top-level is returned,
        when False, then we get detailed metrics for all classes, functions, nested functions, ...
        """
        unit = 1 if unit else 0
        url = f"{self.base_url}/metrics?file_name={filename}&unit={unit}"
        r = requests.post(url, data=code, headers=HEADERS)

        if not r.ok:
            return {}

        return r.json()
