# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Which platforms build the files of mozilla-central, according to the builds of Firefox's CI.

Searchfox's indexing jobs build Firefox for each platform. Their index lists the source files
(including headers) the build compiled, and the build's chrome map the files it packaged (JS, CSS,
...): a file is built on the platforms whose builds list it. A file no build lists (a file added
since the builds, tests, docs, Rust or Java files, ...) is built on the platforms that build files
in its closest ancestor directory, or on all platforms if there's none.

A patch only touching files built on some platforms can only break those (e.g. widget/cocoa only
on macOS, gfx/layers/d3d11 only on Windows).
"""

import io
import logging
import os
import zipfile
from typing import Collection, Iterable, Iterator, Mapping

import requests

logger = logging.getLogger(__name__)

# The OS families, named as test_scheduling.config_platform_family names them.
PLATFORMS = ("android", "linux", "macosx", "windows")
ALL_PLATFORMS = frozenset(PLATFORMS)

# The Searchfox jobs of each platform (gecko.v2.mozilla-central.latest.firefox.<job>), for the
# architectures tests run on.
SEARCHFOX_JOBS = {
    "android": ("android-aarch64-searchfox-debug",),
    "linux": ("linux64-searchfox-debug",),
    "macosx": ("macosx64-searchfox-debug", "macosx64-aarch64-searchfox-debug"),
    "windows": ("win64-searchfox-debug",),
}
# Platforms tested on an architecture their jobs don't build (most Android tests run on x86_64
# emulators, the job builds aarch64): a file built on other platforms (e.g. x86 SIMD code) isn't
# excluded if they build files in its directory.
INCOMPLETE_PLATFORMS = frozenset({"android"})
# Directories of code that some platforms' jobs don't build, although their tests run it: the Android
# job doesn't build gtests (4 files in gtest directories, against ~800 for the desktop jobs).
UNBUILT_DIRECTORIES = {"android": frozenset({"gtest"})}

ARTIFACT_URL = "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.mozilla-central.latest.firefox.{job}/artifacts/public/build/{artifact}"


class _RemoteFile(io.RawIOBase):
    """A file read with HTTP range requests, to list the files of a remote zip without downloading it."""

    def __init__(self, url: str) -> None:
        response = requests.head(url, allow_redirects=True, timeout=60)
        response.raise_for_status()
        self.url = response.url
        self.size = int(response.headers["Content-Length"])
        self.position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        start = {io.SEEK_SET: 0, io.SEEK_CUR: self.position, io.SEEK_END: self.size}
        self.position = start[whence] + offset
        return self.position

    def readinto(self, buffer) -> int:
        end = min(self.size, self.position + len(buffer))
        if end <= self.position:
            return 0
        response = requests.get(
            self.url,
            headers={
                "Range": f"bytes={self.position}-{end - 1}",
                "Accept-Encoding": "identity",
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.content
        buffer[: len(data)] = data
        self.position += len(data)
        return len(data)


def _job_files(job: str) -> set[str]:
    """The files compiled and packaged by a Searchfox job's build."""
    url = ARTIFACT_URL.format(job=job, artifact="target.mozsearch-index.zip")
    with zipfile.ZipFile(_RemoteFile(url)) as index:
        files = {
            name
            for name in index.namelist()
            if not name.endswith("/") and not name.startswith("__GENERATED__/")
        }

    response = requests.get(
        ARTIFACT_URL.format(job=job, artifact="chrome-map.json"), timeout=60
    )
    response.raise_for_status()
    # [chrome URLs, overrides, {installed path: [source path, ...]}, build config]: only the files
    # packaged in the application, not test files installed for the harnesses (e.g. crashtests).
    for installed, sources in response.json()[2].items():
        if (
            installed.startswith("dist/bin/")
            and sources
            and isinstance(sources[0], str)
            and "*" not in sources[0]
        ):
            files.add(sources[0])

    return files


def retrieve_built_files() -> dict[str, list[str]]:
    """The files built on each platform. Platforms whose files can't be retrieved are left out."""
    built = {}
    for platform, jobs in SEARCHFOX_JOBS.items():
        try:
            built[platform] = sorted(set().union(*(_job_files(job) for job in jobs)))
        except Exception:
            logger.exception("Failed to retrieve the files built on %s", platform)
    return built


def _ancestors(path: str) -> Iterator[str]:
    directory = os.path.dirname(path)
    while directory:
        yield directory
        directory = os.path.dirname(directory)


class BuildPlatforms:
    """The platforms building files, from the files built on each platform (see retrieve_built_files)."""

    def __init__(self, built: Mapping[str, Iterable[str]]) -> None:
        self.files = {
            platform: frozenset(files)
            for platform, files in built.items()
            if platform in ALL_PLATFORMS and files
        }
        self.directories = {
            platform: frozenset(d for f in files for d in _ancestors(f))
            for platform, files in self.files.items()
        }
        # The types of the files the builds list: the directory of a file of another type (e.g. the
        # inputs of code generation, like WebIDL or Glean YAML files, or build files) doesn't tell
        # which platforms use it.
        self.types = frozenset(
            os.path.splitext(f)[1] for files in self.files.values() for f in files
        )
        # The platforms whose files are unknown might build anything.
        self.unknown = ALL_PLATFORMS - set(self.files)

    def platforms(self, path: str) -> frozenset[str]:
        return self._platforms(path) | self.unknown | self._unbuilt(path)

    def _platforms(self, path: str) -> frozenset[str]:
        listed = {p for p, files in self.files.items() if path in files}
        if listed:
            directory = os.path.dirname(path)
            listed |= {
                p
                for p in INCOMPLETE_PLATFORMS & set(self.directories)
                if directory in self.directories[p]
            }
            return frozenset(listed)

        if os.path.splitext(path)[1] not in self.types:
            return ALL_PLATFORMS

        for directory in _ancestors(path):
            near = {p for p, dirs in self.directories.items() if directory in dirs}
            if near:
                return frozenset(near)

        return ALL_PLATFORMS

    def _unbuilt(self, path: str) -> frozenset[str]:
        directories = set(path.split("/")[:-1])
        return frozenset(
            p for p, names in UNBUILT_DIRECTORIES.items() if directories & names
        )

    def find_platforms_for_paths(self, paths: Collection[str]) -> frozenset[str]:
        """The platforms building any of `paths`."""
        result: frozenset[str] = frozenset().union(*map(self.platforms, paths))
        return result or ALL_PLATFORMS
