# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
import os

from bugbug import repository


class name(object):
    def __call__(self, test_job, **kwargs):
        return test_job["name"]


class platform(object):
    def __call__(self, test_job, **kwargs):
        platforms = []
        for ps in (("linux",), ("windows", "win"), ("android",), ("macosx",)):
            for p in ps:
                if p in test_job["name"][: test_job["name"].index("/")]:
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

    name = name.split("/")[1]

    return "-".join([p for p in name.split("-") if p not in NAME_PARTS_TO_SKIP])


class chunk(object):
    def __call__(self, test_job, **kwargs):
        return get_chunk(test_job["name"])


class suite(object):
    def __call__(self, test_job, **kwargs):
        return "-".join(
            p for p in get_chunk(test_job["name"]).split("-") if not p.isdigit()
        )


class is_test(object):
    def __call__(self, test_job, **kwargs):
        return test_job["name"].startswith("test-")


class is_build(object):
    def __call__(self, test_job, **kwargs):
        return test_job["name"].startswith("build-")


class prev_failures(object):
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


class touched_together(object):
    def __call__(self, test_job, **kwargs):
        return {
            "touched_together_files": test_job["touched_together_files"],
            "touched_together_directories": test_job["touched_together_directories"],
        }


class arch(object):
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


class path_distance(object):
    def __call__(self, test_job, commit, **kwargs):
        distances = []
        for path in commit["files"]:
            movement = os.path.relpath(
                os.path.dirname(test_job["name"]), os.path.dirname(path)
            )
            if movement == ".":
                distances.append(0)
            else:
                distances.append(movement.count("/") + 1)

        return min(distances, default=None)


class common_path_components(object):
    def __call__(self, test_job, commit, **kwargs):
        test_components = set(test_job["name"].split("/"))
        common_components_numbers = [
            len(set(path.split("/")) & test_components) for path in commit["files"]
        ]
        return max(common_components_numbers, default=None)


class first_common_parent_distance(object):
    def __call__(self, test_job, commit, **kwargs):
        distances = []
        for path in commit["files"]:
            path_components = path.split("/")

            for i in range(len(path_components) - 1, 0, -1):
                if test_job["name"].startswith("/".join(path_components[:i])):
                    distances.append(i)
                    break

        return min(distances, default=None)


class same_component(object):
    def __call__(self, test_job, commit, **kwargs):
        component_mapping = repository.get_component_mapping()

        if test_job["name"].encode("utf-8") not in component_mapping:
            return None

        touches_same_component = any(
            component_mapping[test_job["name"].encode("utf-8")]
            == component_mapping[f.encode("utf-8")]
            for f in commit["files"]
            if f.encode("utf-8") in component_mapping
        )
        return touches_same_component
