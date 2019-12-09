# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import subprocess
import time

import requests

from bugbug import utils


class RustCodeAnalysisServer:
    def __init__(self):
        self.addr = "localhost"
        self.ok = False
        self.headers = {"Content-type": "application/octet-stream"}

        # run the server
        for _ in range(7):
            try:
                self.port = utils.get_free_tcp_port()
                self.proc = subprocess.Popen(
                    [
                        "rust-code-analysis",
                        "--serve",
                        "--port",
                        str(self.port),
                        "--host",
                        self.addr,
                    ]
                )
            except FileNotFoundError:
                raise Exception("rust-code-analysis is required for code analysis")

    def ping(self):
        if self.proc.poll() is not None:
            return

        url = f"{self.base_url}/ping"
        for _ in range(7):
            try:
                r = requests.get(url)
                r.raise_for_status()
                print("Rust code analysis server is ready to accept queries")
                self.ok = True
                return
            except Exception:
                time.sleep(1)

    @property
    def available(self):
        if self.ok:
            return True

        self.ping()
        return self.ok

    @property
    def base_url(self):
        return f"http://{self.addr}:{self.port}"

    def __str__(self):
        return f"Server running at {self.base_url}"

    def metrics(self, filename, code, unit=True):
        unit = 1 if unit else 0
        url = f"{self.base_url}/metrics?file_name={filename}&unit={unit}"
        r = requests.post(url, data=code, headers=self.headers)

        if r.status_code != 404:
            return r.json()
        return {}

    def function(self, filename, code):
        url = f"{self.base_url}/function?file_name={filename}"
        r = requests.post(url, data=code, headers=self.headers)

        if r.status_code != 404:
            return r.json()
        return {}
