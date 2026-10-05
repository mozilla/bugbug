# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import collections
import os
import re

from bugbug import repository, test_scheduling


class Name(object):
    def __call__(self, test_job, **kwargs):
        return test_job["name"]


class Platform(object):
    def __call__(self, test_job, **kwargs):
        platforms = []
        for ps in (
            ("linux",),
            ("windows", "win"),
            ("android", "apk", "fenix", "components", "focus", "klar", "samples"),
            ("macosx",),
            ("ios",),
        ):
            for p in ps:
                if p in test_job["name"].split("/")[0]:
                    platforms.append(ps[0])
                    break
        assert len(platforms) == 1, "Wrong platforms ({}) in {}".format(
            platforms, test_job["name"]
        )
        return platforms[0]


NAME_PARTS_TO_SKIP = ("opt", "debug", "e10s", "1proc")


def get_chunk(name):
    if name.startswith("build-signing-"):
        return "build-signing"
    elif name.startswith("build-"):
        return "build"

    assert name.startswith("test-"), f"{name} should start with test-"

    name = name.split("/")[1] if "/" in name else name

    return "-".join([p for p in name.split("-") if p not in NAME_PARTS_TO_SKIP])


class Chunk(object):
    def __call__(self, test_job, **kwargs):
        return get_chunk(test_job["name"])


class Suite(object):
    def __call__(self, test_job, **kwargs):
        return "-".join(
            p for p in get_chunk(test_job["name"]).split("-") if not p.isdigit()
        )


class IsTest(object):
    def __call__(self, test_job, **kwargs):
        return test_job["name"].startswith("test-")


class IsBuild(object):
    def __call__(self, test_job, **kwargs):
        return test_job["name"].startswith("build-")


class TaskNameTokens(object):
    """The tokens of the task label (split on "-" and "/"), without the numbers (e.g. chunks), as features.

    E.g. test-linux2404-64/debug-gtest-1proc has the tokens test, linux2404, debug, gtest and 1proc.
    Unlike the task name itself, they're shared by related tasks (e.g. all debug tasks, all asan
    builds, all gtest tasks). The model learns which tokens matter, so new tasks and naming changes
    don't need any update.
    """

    def __call__(self, test_job, **kwargs):
        return [
            token
            for token in re.split(r"[-/]", test_job["name"])
            if token and not token.isdigit()
        ]


# Build-relevant file types and directories, for PushBuildFiles.
# Build-relevant file types: (suffixes, file names, path substrings). A file has the first matching type.
_BUILD_FILE_TYPES = {
    "build": (
        ("moz.build", ".mozbuild", ".mk", ".in", "configure", ".m4", ".gn", ".gni"),
        ("configure.py", "GNUmakefile"),
        ("/mozconfig",),
    ),
    # Dependencies: Rust crates, Python packages, Gradle libraries, audits of vendored crates.
    "deps": (
        (),
        (
            "Cargo.toml",
            "Cargo.lock",
            "pyproject.toml",
            "uv.lock",
            "libs.versions.toml",
            "imports.lock",
        ),
        (),
    ),
    # Other TOML files, mostly test manifests (which the build processes).
    "manifests": ((".toml",), (), ()),
    "java": ((".java", ".kt", ".gradle", ".kts"), ("gradle.properties", "gradlew"), ()),
    "swift": ((".swift",), (), ()),
    "rs": ((".rs",), (), ()),
}
# Files changing the build system, for PushTaskFiles.
_BUILD_SYSTEM_TYPES = ("build", "deps")
# The OS of a task, from the start of its platform (the label without the task kind).
_OS_PATTERNS = (
    (
        "android",
        re.compile(
            r"^(android|apk|bundle|fenix|focus|klar|components|geckoview|samples|fat-aar)"
        ),
    ),
    ("ios", re.compile(r"^ios")),
    ("macosx", re.compile(r"^(macosx|osx|mac)")),
    ("windows", re.compile(r"^(windows|win|mingw)")),
    ("linux", re.compile(r"^(linux|sm-|spidermonkey)")),
)
# The names of the directories with the platform-specific code of each OS (e.g. widget/cocoa,
# accessible/mac, security/sandbox/win). "unix" directories (e.g. xpcom/reflect/xptcall/md/unix) are
# shared by Linux and macOS.
_OS_DIR_NAMES = {
    "android": frozenset({"android"}),
    "ios": frozenset({"ios", "uikit"}),
    "macosx": frozenset({"cocoa", "mac", "macos", "macosx", "osx", "unix"}),
    "windows": frozenset({"windows", "win", "win32"}),
    "linux": frozenset({"gtk", "linux", "unix", "x11", "wayland", "atk", "gnome"}),
}
# More platform-specific files (path prefixes, file types), for test tasks.
_TEST_PLATFORM_FILES = {
    "android": (("mobile/shared/",), (".java", ".kt", ".gradle", ".kts")),
    "ios": ((), (".swift",)),
    "macosx": ((), (".mm",)),
}
# The harness and tests of the test suites selected by the label model (the first matching substring of the
# task name wins). Suites run per manifest (e.g. mochitest, xpcshell, reftest, web-platform-tests) are selected
# by the group model, and some (e.g. jittest, jsreftest) aren't selected by bugbug at all
# (test_scheduling.JOBS_TO_IGNORE), so they aren't here. Patterns starting with "/" match anywhere in the path,
# the others match its start. For gtest, cppunittest, marionette, telemetry-tests and firefox-ui, these are
# only the harnesses: the directories of their tests come from the Firefox tree when training
# (test_scheduling.get_suite_test_dirs).
_SUITE_FILES = (
    ("appservices", ("third_party/application-services/",)),
    ("geckoview-junit", ("mobile/android/geckoview/",)),
    ("gtest", ("testing/gtest/",)),
    ("cppunittest", ("testing/cppunittest", "testing/runcppunittests.py")),
    ("marionette", ("testing/marionette/", "remote/marionette/")),
    ("telemetry-tests", ("toolkit/components/telemetry/tests/marionette/",)),
    ("firefox-ui", ("testing/firefox-ui/",)),
    ("web-platform-tests-print-reftest", ("/print", "/Print", "/nsPrint")),
    ("devtools-compat", ("devtools/",)),
    ("crashtest", ("/crashtests/", "testing/reftest/")),
    ("reftest", ("layout/reftests/", "testing/reftest/")),
    ("test-apk-fenix", ("mobile/android/fenix/",)),
    ("test-apk-focus", ("mobile/android/focus-android/",)),
    ("test-apk-klar", ("mobile/android/focus-android/",)),
    ("test-apk", ("mobile/android/",)),
    ("test-components", ("mobile/android/android-components/",)),
)


def get_task_os(name: str) -> str:
    """The OS of a task (android, ios, macosx, windows or linux), or "other"."""
    platform = re.sub(r"^(build-signing-|build-|test-)", "", name).partition("/")[0]
    return next(
        (os_ for os_, pattern in _OS_PATTERNS if pattern.search(platform)), "other"
    )


def get_task_source_dir(name: str) -> str | None:
    """The source directory of an Android component, sample or app task, if any.

    E.g. build-components-feature-logins and test-components-android-feature-logins are built from
    mobile/android/android-components/components/feature/logins/.
    """
    m = re.match(r"^(?:build|test)-components-(?:android-)?([a-z]+)-(.+)$", name)
    if m:
        return (
            f"mobile/android/android-components/components/{m.group(1)}/{m.group(2)}/"
        )
    m = re.match(r"^build-samples-(.+)$", name)
    if m:
        return f"mobile/android/android-components/samples/{m.group(1)}/"
    m = re.match(r"^(?:build|test)-apk-(fenix|focus|klar)", name)
    if m:
        return (
            "mobile/android/fenix/"
            if m.group(1) == "fenix"
            else "mobile/android/focus-android/"
        )
    return None


def _file_type(f: str) -> str | None:
    name = os.path.basename(f)
    for file_type, (suffixes, names, substrings) in _BUILD_FILE_TYPES.items():
        if f.endswith(suffixes) or name in names or any(s in f for s in substrings):
            return file_type
    return None


def _file_oses(f: str) -> list[str]:
    dirs = set(f.split("/")[:-1])
    return [os_ for os_, names in _OS_DIR_NAMES.items() if dirs & names]


def _is_build(name: str) -> bool:
    return name.startswith("build-") and not name.startswith("build-signing-")


class PushBuildFiles(object):
    """For build tasks, the number of modified files of each build-relevant type and in the code of each OS.

    E.g. a build system or toolchain change can break any build, a change under mobile/android the
    Android builds. Test tasks don't get them: for them, they'd be a push-level "risky push" signal,
    which moves the selection budget away from the tests related to the change.
    """

    def __call__(self, test_job, commit, **kwargs):
        if not _is_build(test_job["name"]):
            return None

        files = commit["files"]
        types = collections.Counter(_file_type(f) for f in files)
        features = {f"push_build_files_type_{t}": types[t] for t in _BUILD_FILE_TYPES}
        oses = collections.Counter(os_ for f in files for os_ in _file_oses(f))
        features.update({f"push_build_files_os_{o}": oses[o] for o in _OS_DIR_NAMES})
        return features


class PushTaskFiles(object):
    """The number of modified files related to the task.

    For every task, the files in the platform-specific code of its OS, and in its source directory
    (for Android components, samples and apps); for build tasks, the files changing the build
    system; for test tasks, the files in the platform-specific code of its OS (including
    platform-specific file types, e.g. .java for Android) and in its suite's harness and tests.
    """

    def __init__(self, suite_test_dirs: dict[str, tuple[str, ...]] | None = None):
        # The directories with the tests of some suites (see test_scheduling.get_suite_test_dirs), in
        # addition to their harnesses.
        suite_test_dirs = suite_test_dirs or {}
        self.suite_files = tuple(
            (key, patterns + suite_test_dirs.get(key, ()))
            for key, patterns in _SUITE_FILES
        )

    def __call__(self, test_job, commit, **kwargs):
        name = test_job["name"]
        files = commit["files"]
        os_ = get_task_os(name)
        is_platform_file = [os_ in _file_oses(f) for f in files]
        features = {"push_platform_files": sum(is_platform_file)}

        source_dir = get_task_source_dir(name)
        if source_dir is not None:
            features["push_source_files"] = sum(
                1 for f in files if f.startswith(source_dir)
            )

        if _is_build(name):
            features["push_build_system_files"] = sum(
                1 for f in files if _file_type(f) in _BUILD_SYSTEM_TYPES
            )
        elif name.startswith("test-"):
            prefixes, extensions = _TEST_PLATFORM_FILES.get(os_, ((), ()))
            features["push_test_platform_files"] = sum(
                1
                for f, is_platform in zip(files, is_platform_file)
                if is_platform
                or (prefixes and f.startswith(prefixes))
                or (extensions and f.endswith(extensions))
            )
            patterns = next((p for key, p in self.suite_files if key in name), ())
            features["push_suite_files"] = sum(
                1
                for f in files
                if any(
                    (p in f) if p.startswith("/") else f.startswith(p) for p in patterns
                )
            )

        return features


class PrevFailures(object):
    def __call__(self, test_job, **kwargs):
        return {
            "total": test_job["failures"],
            "past_700_pushes": test_job["failures_past_700_pushes"],
            "past_1400_pushes": test_job["failures_past_1400_pushes"],
            "past_2800_pushes": test_job["failures_past_2800_pushes"],
            "in_types": test_job["failures_in_types"],
            "past_700_pushes_in_types": test_job["failures_past_700_pushes_in_types"],
            "past_1400_pushes_in_types": test_job["failures_past_1400_pushes_in_types"],
            "past_2800_pushes_in_types": test_job["failures_past_2800_pushes_in_types"],
            "in_files": test_job["failures_in_files"],
            "past_700_pushes_in_files": test_job["failures_past_700_pushes_in_files"],
            "past_1400_pushes_in_files": test_job["failures_past_1400_pushes_in_files"],
            "past_2800_pushes_in_files": test_job["failures_past_2800_pushes_in_files"],
            "in_directories": test_job["failures_in_directories"],
            # "past_700_pushes_in_directories": test_job[
            #     "failures_past_700_pushes_in_directories"
            # ],
            # "past_1400_pushes_in_directories": test_job[
            #     "failures_past_1400_pushes_in_directories"
            # ],
            # "past_2800_pushes_in_directories": test_job[
            #     "failures_past_2800_pushes_in_directories"
            # ],
            # "in_components": test_job["failures_in_components"],
            # "past_100_pushes_in_components": test_job[
            #     "failures_past_100_pushes_in_components"
            # ],
            # "past_200_pushes_in_components": test_job[
            #     "failures_past_200_pushes_in_components"
            # ],
            # "past_300_pushes_in_components": test_job[
            #     "failures_past_300_pushes_in_components"
            # ],
            # "past_700_pushes_in_components": test_job[
            #     "failures_past_700_pushes_in_components"
            # ],
            # "past_1400_pushes_in_components": test_job[
            #     "failures_past_1400_pushes_in_components"
            # ],
            # "past_2800_pushes_in_components": test_job[
            #     "failures_past_2800_pushes_in_components"
            # ],
        }


class TouchedTogether(object):
    def __call__(self, test_job, **kwargs):
        return {
            "touched_together_files": test_job["touched_together_files"],
            "touched_together_directories": test_job["touched_together_directories"],
            # Pointwise mutual information of the co-changes (see test_scheduling.get_cochange_pmi).
            "touched_together_pmi": test_job.get("touched_together_pmi", 0.0),
        }


class Arch(object):
    def __call__(self, test_job, **kwargs):
        if "build-" in test_job["name"]:
            return []
        archs = set()  # Used set to eliminate duplicates like in case of aarch64
        for arcs in (
            ("arm", "arm7"),
            ("aarch64", "arm64"),
            ("64", "x86_64"),
            ("32", "x86", "i386"),
        ):
            for a in arcs:
                if a in test_job["name"][: test_job["name"].index("/")]:
                    if a == "64" and "aarch64" in archs:
                        continue
                    elif a == "x86" and "64" in archs:
                        continue
                    archs.add(arcs[0])
        assert len(archs) == 1, "Wrong architectures ({}) in {}".format(
            archs, test_job["name"]
        )
        return archs.pop()


def get_manifest(runnable):
    if isinstance(runnable, str):
        return runnable
    else:
        return runnable[1]


def commonprefix(path1, path2):
    for i, c in enumerate(path1):
        if i + 1 > len(path2) or c != path2[i]:
            return path1[:i]
    return path1


class PathDistance(object):
    def __call__(self, test_job, commit, **kwargs):
        min_distance = None

        manifest = get_manifest(test_job["name"])

        for path in commit["files"]:
            i = len(commonprefix(manifest, path))
            distance = manifest[i:].count("/") + path[i:].count("/")

            if min_distance is None or min_distance > distance:
                min_distance = distance

        return min_distance


class CommonPathComponents(object):
    def __call__(self, test_job, commit, **kwargs):
        manifest = get_manifest(test_job["name"])
        test_components = set(manifest.split("/"))
        common_components_numbers = (
            len(set(path.split("/")) & test_components) for path in commit["files"]
        )
        return max(common_components_numbers, default=None)


class FirstCommonParentDistance(object):
    def __call__(self, test_job, commit, **kwargs):
        min_distance = None

        manifest = get_manifest(test_job["name"])

        for path in commit["files"]:
            path_components = path.split("/")

            for i in range(len(path_components) - 1, 0, -1):
                if manifest.startswith("/".join(path_components[:i])):
                    if min_distance is None or min_distance > i:
                        min_distance = i
                    break

        return min_distance


class SameComponent(object):
    def __call__(self, test_job, commit, **kwargs):
        manifest = get_manifest(test_job["name"])

        component_mapping = repository.get_component_mapping()

        if manifest.encode("utf-8") not in component_mapping:
            return None

        touches_same_component = any(
            component_mapping[manifest.encode("utf-8")]
            == component_mapping[f.encode("utf-8")]
            for f in commit["files"]
            if f.encode("utf-8") in component_mapping
        )
        return touches_same_component


class ManifestSuite(object):
    def __call__(self, test_job, commit, **kwargs):
        manifest = get_manifest(test_job["name"])

        if manifest.startswith("testing/web-platform/"):
            return "WPT"

        base = os.path.basename(manifest)

        if any(s in base for s in ("chrome", "browser", "mochitest", "a11y")):
            return "mochitest"
        elif base == "jstests.list":
            return "jstest"
        elif "xpcshell" in base:
            return "xpcshell"
        elif "crashtest" in base:
            return "crashtest"
        elif "reftest" in base or base.endswith(".list"):
            return "reftest"

        # Manifests with non-standard names (e.g. devtools' split browser
        # manifests) can be identified by the directory they live in.
        if base.endswith(".toml"):
            parent = os.path.basename(os.path.dirname(manifest))
            if parent == "xpcshell":
                return "xpcshell"
            elif parent in ("browser", "mochitest"):
                return "mochitest"

        return None


class TouchedGroupDirs(object):
    """Number of modified files in the group's directories, and in their parent directory.

    This mostly overlaps with the rule that always schedules the manifests including the modified
    tests (test_scheduling.find_manifests_for_paths), but also covers nearby changes.
    """

    def __call__(self, test_job, commit, **kwargs):
        dirs = test_scheduling.get_runnable_dirs(get_manifest(test_job["name"]))
        prefixes = tuple(f"{d}/" for d in dirs)
        parent = f"{os.path.dirname(dirs[0])}/"
        return {
            "touch_group_dirs": sum(
                1 for f in commit["files"] if f.startswith(prefixes)
            ),
            "touch_parent_dir": sum(1 for f in commit["files"] if f.startswith(parent)),
        }
