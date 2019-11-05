# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.


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


def get_chunk(name):
    if name.startswith("build-"):
        return "build"

    assert name.startswith("test-"), f"{name} should start with test-"

    if "/debug-" in name:
        name = name[name.index("/debug-") + 7 :]
    elif "/opt-" in name:
        name = name[name.index("/opt-") + 5 :]
    elif "/pgo-" in name:
        name = name[name.index("/pgo-") + 5 :]
    else:
        assert False, f"{name} should be either debug or opt"

    return "-".join(p for p in name.split("-") if p != "e10s")


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
            "past_7_pushes": test_job["failures_past_7_pushes"],
            "past_14_pushes": test_job["failures_past_14_pushes"],
            "past_28_pushes": test_job["failures_past_28_pushes"],
            "past_56_pushes": test_job["failures_past_56_pushes"],
            "in_types": test_job["failures_in_types"],
            "past_7_pushes_in_types": test_job["failures_past_7_pushes_in_types"],
            "past_14_pushes_in_types": test_job["failures_past_14_pushes_in_types"],
            "past_28_pushes_in_types": test_job["failures_past_28_pushes_in_types"],
            "past_56_pushes_in_types": test_job["failures_past_56_pushes_in_types"],
            "in_files": test_job["failures_in_files"],
            "past_7_pushes_in_files": test_job["failures_past_7_pushes_in_files"],
            "past_14_pushes_in_files": test_job["failures_past_14_pushes_in_files"],
            "past_28_pushes_in_files": test_job["failures_past_28_pushes_in_files"],
            "past_56_pushes_in_files": test_job["failures_past_56_pushes_in_files"],
            "in_directories": test_job["failures_in_directories"],
            "past_7_pushes_in_directories": test_job[
                "failures_past_7_pushes_in_directories"
            ],
            "past_14_pushes_in_directories": test_job[
                "failures_past_14_pushes_in_directories"
            ],
            "past_28_pushes_in_directories": test_job[
                "failures_past_28_pushes_in_directories"
            ],
            "past_56_pushes_in_directories": test_job[
                "failures_past_56_pushes_in_directories"
            ],
            "in_components": test_job["failures_in_components"],
            "past_7_pushes_in_components": test_job[
                "failures_past_7_pushes_in_components"
            ],
            "past_14_pushes_in_components": test_job[
                "failures_past_14_pushes_in_components"
            ],
            "past_28_pushes_in_components": test_job[
                "failures_past_28_pushes_in_components"
            ],
            "past_56_pushes_in_components": test_job[
                "failures_past_56_pushes_in_components"
            ],
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
