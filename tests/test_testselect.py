# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import itertools
import math
import pickle
from typing import Iterator

import hypothesis
import hypothesis.strategies as st
import numpy as np
import pytest
from igraph import Graph

from bugbug import test_scheduling
from bugbug.models import testselect
from bugbug.utils import LMDBDict


@pytest.fixture
def failing_together() -> Iterator[LMDBDict]:
    yield test_scheduling.get_failing_together_db("label", False)
    test_scheduling.close_failing_together_db("label")


@pytest.fixture
def failing_together_config_group() -> Iterator[LMDBDict]:
    yield test_scheduling.get_failing_together_db("config_group", False)
    test_scheduling.close_failing_together_db("config_group")


def test_reduce1(failing_together: LMDBDict) -> None:
    failing_together[b"test-linux1804-64/debug"] = pickle.dumps(
        {
            "test-windows10/debug": (0.1, 1.0),
            "test-windows10/opt": (0.1, 1.0),
            "test-linux1804-64/opt": (0.1, 1.0),
        }
    )
    failing_together[b"test-linux1804-64/opt"] = pickle.dumps(
        {
            "test-windows10/opt": (0.1, 0.91),
        }
    )
    failing_together[b"test-linux1804-64-asan/debug"] = pickle.dumps(
        {
            "test-linux1804-64/debug": (0.1, 1.0),
        }
    )

    assert testselect.reduce_configs({"test-linux1804-64/debug"}, 1.0) == {
        "test-linux1804-64/debug"
    }
    assert testselect.reduce_configs(
        {"test-linux1804-64/debug", "test-windows10/debug"}, 1.0
    ) == {"test-linux1804-64/debug"}
    assert testselect.reduce_configs(
        {"test-linux1804-64/debug", "test-windows10/opt"}, 1.0
    ) == {"test-linux1804-64/debug"}
    assert testselect.reduce_configs(
        {"test-linux1804-64/opt", "test-windows10/opt"}, 1.0
    ) == {
        "test-linux1804-64/opt",
        "test-windows10/opt",
    }
    assert testselect.reduce_configs(
        {"test-linux1804-64/opt", "test-windows10/opt"}, 0.9
    ) == {"test-linux1804-64/opt"}
    assert testselect.reduce_configs(
        {"test-linux1804-64/opt", "test-linux1804-64/debug"}, 1.0
    ) == {"test-linux1804-64/opt"}
    assert testselect.reduce_configs(
        {"test-linux1804-64-asan/debug", "test-linux1804-64/debug"}, 1.0
    ) == {"test-linux1804-64/debug"}

    # Test case where the second task is not present in the failing together stats of the first.
    assert testselect.reduce_configs(
        {"test-linux1804-64-asan/debug", "test-windows10/opt"}, 1.0
    ) == {"test-linux1804-64-asan/debug", "test-windows10/opt"}

    # Test case where a task is not present at all in the failing together DB.
    assert testselect.reduce_configs(
        {"test-linux1804-64-qr/debug", "test-windows10/opt"}, 1.0
    ) == {
        "test-linux1804-64-qr/debug",
        "test-windows10/opt",
    }


def test_reduce2(failing_together: LMDBDict) -> None:
    failing_together[b"windows10/opt-a"] = pickle.dumps(
        {
            "windows10/opt-b": (0.1, 1.0),
            "windows10/opt-c": (0.1, 0.3),
            "windows10/opt-d": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-b"] = pickle.dumps(
        {
            "windows10/opt-c": (0.1, 1.0),
            "windows10/opt-d": (0.1, 0.3),
        }
    )
    test_scheduling.close_failing_together_db("label")

    assert testselect.reduce_configs(
        {"windows10/opt-a", "windows10/opt-b", "windows10/opt-c", "windows10/opt-d"},
        1.0,
    ) == {
        "windows10/opt-b",
    }


def test_reduce3(failing_together: LMDBDict) -> None:
    failing_together[b"windows10/opt-a"] = pickle.dumps(
        {
            "windows10/opt-b": (0.1, 1.0),
            "windows10/opt-c": (0.1, 0.3),
            "windows10/opt-d": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-b"] = pickle.dumps(
        {
            "windows10/opt-c": (0.1, 1.0),
            "windows10/opt-d": (0.1, 0.3),
        }
    )
    failing_together[b"windows10/opt-c"] = pickle.dumps(
        {
            "windows10/opt-d": (0.1, 1.0),
        }
    )

    result = testselect.reduce_configs(
        {"windows10/opt-a", "windows10/opt-b", "windows10/opt-c", "windows10/opt-d"},
        1.0,
    )
    assert (
        result
        == {
            "windows10/opt-a",
            "windows10/opt-c",
        }
        or result
        == {
            "windows10/opt-d",
            "windows10/opt-c",
        }
        or result
        == {
            "windows10/opt-b",
            "windows10/opt-c",
        }
        or result
        == {
            "windows10/opt-b",
            "windows10/opt-d",
        }
    )


def test_reduce4(failing_together: LMDBDict) -> None:
    failing_together[b"windows10/opt-a"] = pickle.dumps(
        {
            "windows10/opt-b": (0.1, 1.0),
            "windows10/opt-c": (0.1, 0.3),
            "windows10/opt-d": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-b"] = pickle.dumps(
        {
            "windows10/opt-c": (0.1, 1.0),
            "windows10/opt-d": (0.1, 0.3),
            "windows10/opt-e": (0.1, 1.0),
        }
    )

    result = testselect.reduce_configs(
        {
            "windows10/opt-a",
            "windows10/opt-b",
            "windows10/opt-c",
            "windows10/opt-d",
            "windows10/opt-e",
        },
        1.0,
    )
    assert result == {
        "windows10/opt-e",
    } or result == {
        "windows10/opt-b",
    }


def test_reduce5(failing_together: LMDBDict) -> None:
    failing_together[b"linux1804-64/opt-a"] = pickle.dumps(
        {
            "windows10/opt-d": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-c"] = pickle.dumps(
        {
            "windows10/opt-d": (0.1, 1.0),
        }
    )

    result = testselect.reduce_configs(
        {"linux1804-64/opt-a", "windows10/opt-c", "windows10/opt-d"}, 1.0
    )
    assert result == {
        "windows10/opt-d",
    }


def test_reduce6(failing_together: LMDBDict) -> None:
    failing_together[b"windows10/opt-a"] = pickle.dumps(
        {
            "windows10/opt-d": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-c"] = pickle.dumps(
        {
            "windows10/opt-d": (0.1, 1.0),
        }
    )

    result = testselect.reduce_configs(
        {
            "windows10/opt-a",
            "windows10/opt-b",
            "windows10/opt-c",
            "windows10/opt-d",
            "windows10/opt-e",
        },
        1.0,
    )
    assert (
        result
        == {
            "windows10/opt-a",
            "windows10/opt-b",
            "windows10/opt-e",
        }
        or result
        == {
            "windows10/opt-c",
            "windows10/opt-b",
            "windows10/opt-e",
        }
        or result
        == {
            "windows10/opt-d",
            "windows10/opt-b",
            "windows10/opt-e",
        }
    )


def test_reduce7(failing_together: LMDBDict) -> None:
    failing_together[b"windows10/opt-1"] = pickle.dumps(
        {
            "windows10/opt-3": (0.1, 0.0),
            "windows10/opt-5": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-3"] = pickle.dumps(
        {
            "windows10/opt-5": (0.1, 1.0),
        }
    )

    result = testselect.reduce_configs(
        {
            "windows10/opt-1",
            "windows10/opt-3",
            "windows10/opt-5",
        },
        1.0,
    )
    assert result == {"windows10/opt-5"}


def test_reduce8(failing_together: LMDBDict) -> None:
    failing_together[b"windows10/opt-1"] = pickle.dumps(
        {
            "windows10/opt-5": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-2"] = pickle.dumps(
        {
            "windows10/opt-6": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-3"] = pickle.dumps(
        {
            "windows10/opt-4": (0.1, 1.0),
            "windows10/opt-5": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-4"] = pickle.dumps(
        {
            "windows10/opt-6": (0.1, 1.0),
        }
    )

    result = testselect.reduce_configs(
        {
            "windows10/opt-0",
            "windows10/opt-1",
            "windows10/opt-2",
            "windows10/opt-3",
            "windows10/opt-4",
            "windows10/opt-5",
            "windows10/opt-6",
        },
        1.0,
    )
    assert result == {"windows10/opt-0", "windows10/opt-5", "windows10/opt-6"}


def test_reduce9(failing_together: LMDBDict) -> None:
    failing_together[b"windows10/opt-0"] = pickle.dumps(
        {
            "windows10/opt-5": (0.1, 0.0),
        }
    )
    failing_together[b"windows10/opt-1"] = pickle.dumps(
        {
            "windows10/opt-5": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-2"] = pickle.dumps(
        {
            "windows10/opt-6": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-3"] = pickle.dumps(
        {
            "windows10/opt-4": (0.1, 1.0),
            "windows10/opt-5": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-4"] = pickle.dumps(
        {
            "windows10/opt-6": (0.1, 1.0),
        }
    )

    result = testselect.reduce_configs(
        {
            "windows10/opt-0",
            "windows10/opt-1",
            "windows10/opt-2",
            "windows10/opt-3",
            "windows10/opt-4",
            "windows10/opt-5",
            "windows10/opt-6",
        },
        1.0,
    )
    assert result == {"windows10/opt-0", "windows10/opt-5", "windows10/opt-6"}


def test_reduce10(failing_together: LMDBDict) -> None:
    failing_together[b"windows10/opt-3"] = pickle.dumps(
        {
            "windows10/opt-5": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-4"] = pickle.dumps(
        {
            "windows10/opt-6": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-5"] = pickle.dumps(
        {
            "windows10/opt-6": (0.1, 1.0),
        }
    )

    result = testselect.reduce_configs(
        {
            "windows10/opt-0",
            "windows10/opt-1",
            "windows10/opt-2",
            "windows10/opt-3",
            "windows10/opt-4",
            "windows10/opt-5",
            "windows10/opt-6",
        },
        1.0,
    )
    assert result == {
        "windows10/opt-0",
        "windows10/opt-1",
        "windows10/opt-2",
        "windows10/opt-3",
        "windows10/opt-6",
    } or result == {
        "windows10/opt-0",
        "windows10/opt-1",
        "windows10/opt-2",
        "windows10/opt-4",
        "windows10/opt-5",
    }


def test_reduce11(failing_together: LMDBDict) -> None:
    failing_together[b"windows10/opt-1"] = pickle.dumps(
        {
            "windows10/opt-2": (0.1, 0.0),
            "windows10/opt-3": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-2"] = pickle.dumps(
        {
            "windows10/opt-3": (0.1, 1.0),
        }
    )

    result = testselect.reduce_configs(
        {
            "windows10/opt-1",
            "windows10/opt-2",
            "windows10/opt-3",
        },
        1.0,
    )
    assert result == {"windows10/opt-3"}


def test_reduce12(failing_together: LMDBDict) -> None:
    failing_together[b"windows10/opt-0"] = pickle.dumps(
        {
            "windows10/opt-1": (0.1, 0.0),
            "windows10/opt-2": (0.1, 0.0),
            "windows10/opt-3": (0.1, 0.0),
            "windows10/opt-4": (0.1, 0.0),
            "windows10/opt-5": (0.1, 0.0),
        }
    )
    failing_together[b"windows10/opt-1"] = pickle.dumps(
        {
            "windows10/opt-2": (0.1, 0.0),
            "windows10/opt-3": (0.1, 0.0),
            "windows10/opt-4": (0.1, 0.0),
            "windows10/opt-5": (0.1, 0.0),
        }
    )
    failing_together[b"windows10/opt-2"] = pickle.dumps(
        {
            "windows10/opt-3": (0.1, 0.0),
            "windows10/opt-4": (0.1, 1.0),
            "windows10/opt-5": (0.1, 0.0),
        }
    )
    failing_together[b"windows10/opt-3"] = pickle.dumps(
        {
            "windows10/opt-4": (0.1, 0.0),
            "windows10/opt-5": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-4"] = pickle.dumps(
        {
            "windows10/opt-5": (0.1, 1.0),
        }
    )

    result = testselect.reduce_configs(
        {
            "windows10/opt-0",
            "windows10/opt-1",
            "windows10/opt-2",
            "windows10/opt-3",
            "windows10/opt-4",
            "windows10/opt-5",
        },
        1.0,
    )
    assert result == {
        "windows10/opt-0",
        "windows10/opt-1",
        "windows10/opt-2",
        "windows10/opt-5",
    } or result == {
        "windows10/opt-0",
        "windows10/opt-1",
        "windows10/opt-3",
        "windows10/opt-4",
    }


def test_reduce13(failing_together: LMDBDict) -> None:
    failing_together[b"windows10/opt-2"] = pickle.dumps(
        {
            "windows10/opt-3": (0.1, 0.0),
            "windows10/opt-4": (0.1, 1.0),
            "windows10/opt-5": (0.1, 0.0),
        }
    )
    failing_together[b"windows10/opt-3"] = pickle.dumps(
        {
            "windows10/opt-4": (0.1, 0.0),
            "windows10/opt-5": (0.1, 1.0),
        }
    )

    result = testselect.reduce_configs(
        {
            "windows10/opt-2",
            "windows10/opt-3",
            "windows10/opt-4",
            "windows10/opt-5",
        },
        1.0,
        True,
    )
    assert result == {"windows10/opt-2", "windows10/opt-5"} or result == {
        "windows10/opt-3",
        "windows10/opt-4",
    }


def test_reduce14(failing_together: LMDBDict) -> None:
    failing_together[b"windows10/opt-1"] = pickle.dumps(
        {
            "windows10/opt-3": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-2"] = pickle.dumps(
        {
            "windows10/opt-4": (0.1, 1.0),
        }
    )
    failing_together[b"windows10/opt-3"] = pickle.dumps(
        {
            "windows10/opt-4": (0.1, 1.0),
        }
    )

    result = testselect.reduce_configs(
        {
            "windows10/opt-1",
            "windows10/opt-2",
            "windows10/opt-3",
            "windows10/opt-4",
        },
        1.0,
        True,
    )
    assert (
        result == {"windows10/opt-1"}
        or result == {"windows10/opt-2"}
        or result == {"windows10/opt-3"}
        or result == {"windows10/opt-4"}
    )


@st.composite
def equivalence_graph(draw) -> Graph:
    NODES = 7

    n = draw(st.integers(min_value=1, max_value=NODES))
    combinations_num = math.factorial(NODES) // (2 * math.factorial(NODES - 2))
    e = draw(
        st.lists(
            st.integers(min_value=0, max_value=1),
            min_size=combinations_num,
            max_size=combinations_num,
        )
    )

    g = Graph()
    g.add_vertices(n)
    for i, (v1, v2) in enumerate(itertools.combinations(range(n), 2)):
        if e[i]:
            g.add_edge(v1, v2)

    hypothesis.note(f"Graph: {g}")
    hypothesis.note(f"Graph Components: {g.components()}")
    return g


@pytest.mark.xfail
@hypothesis.settings(max_examples=7777)
@hypothesis.given(g=equivalence_graph())
def test_all(g: Graph) -> None:
    tasks = [f"windows10/opt-{chr(i)}" for i in range(len(g.vs))]

    try:
        test_scheduling.close_failing_together_db("label")
    except AssertionError:
        pass
    test_scheduling.remove_failing_together_db("label")

    # TODO: Also add some couples that are *not* failing together.
    ft: dict[str, dict[str, tuple[float, float]]] = {}

    for edge in g.es:
        task1 = tasks[edge.tuple[0]]
        task2 = tasks[edge.tuple[1]]
        assert task1 < task2
        if task1 not in ft:
            ft[task1] = {}
        ft[task1][task2] = (0.1, 1.0)

    failing_together = test_scheduling.get_failing_together_db("label", False)
    for t, ts in ft.items():
        failing_together[t.encode("ascii")] = pickle.dumps(ts)

    test_scheduling.close_failing_together_db("label")

    result = testselect.reduce_configs(tasks, 1.0)
    hypothesis.note(f"Result: {sorted(result)}")
    assert len(result) == len(g.components())


def test_select_configs(failing_together_config_group: LMDBDict) -> None:
    past_failures_data = test_scheduling.PastFailures("group", False)
    past_failures_data.all_runnables = ["group1", "group2", "group3"]
    past_failures_data.close()

    failing_together_config_group[b"group1"] = pickle.dumps(
        {
            "linux2404-64-asan/debug": {
                "linux2404-64/debug": (1.0, 0.0),
                "linux2404-64/opt": (1.0, 0.0),
                "mac/debug": (1.0, 0.0),
                "windows10/debug": (1.0, 0.0),
            },
            "linux2404-64/debug": {
                "linux2404-64/opt": (1.0, 1.0),
                "mac/debug": (1.0, 1.0),
                "windows10/debug": (1.0, 1.0),
            },
            "linux2404-64/opt": {
                "mac/debug": (1.0, 1.0),
                "windows10/debug": (1.0, 1.0),
            },
            "mac/debug": {"windows10/debug": (1.0, 1.0)},
        }
    )
    failing_together_config_group[b"group2"] = pickle.dumps(
        {
            "linux2404-64-asan/debug": {
                "linux2404-64/debug": (1.0, 1.0),
                "linux2404-64/opt": (1.0, 0.0),
                "mac/debug": (1.0, 0.0),
                "windows10/debug": (1.0, 0.0),
            },
            "linux2404-64/debug": {
                "linux2404-64/opt": (1.0, 0.0),
                "mac/debug": (1.0, 0.0),
                "windows10/debug": (1.0, 1.0),
            },
            "linux2404-64/opt": {
                "mac/debug": (1.0, 0.0),
                "windows10/debug": (1.0, 0.0),
            },
            "mac/debug": {"windows10/debug": (1.0, 0.0)},
        }
    )
    failing_together_config_group[b"group3"] = pickle.dumps(
        {
            "linux2404-64-asan/debug": {
                "linux2404-64/debug": (1.0, 1.0),
                "linux2404-64/opt": (1.0, 1.0),
                "mac/debug": (1.0, 1.0),
                "windows10/debug": (1.0, 0.0),
            },
            "linux2404-64/debug": {
                "linux2404-64/opt": (1.0, 1.0),
                "mac/debug": (1.0, 1.0),
                "windows10/debug": (1.0, 0.0),
            },
            "linux2404-64/opt": {
                "mac/debug": (1.0, 1.0),
                "windows10/debug": (1.0, 0.0),
            },
            "mac/debug": {"windows10/debug": (1.0, 1.0)},
        }
    )
    failing_together_config_group[b"$ALL_CONFIGS$"] = pickle.dumps(
        [
            "linux2404-64-asan/debug",
            "linux2404-64/debug",
            "linux2404-64/opt",
            "mac/debug",
            "windows10/debug",
        ]
    )
    failing_together_config_group[b"$CONFIGS_BY_GROUP$"] = pickle.dumps(
        {
            "group1": {
                "linux2404-64-asan/debug",
                "linux2404-64/debug",
                "linux2404-64/opt",
                "mac/debug",
                "windows10/debug",
            },
            "group2": {
                "linux2404-64-asan/debug",
                "linux2404-64/debug",
                "linux2404-64/opt",
                "mac/debug",
                "windows10/debug",
            },
            "group3": {
                "linux2404-64-asan/debug",
                "linux2404-64/debug",
                "linux2404-64/opt",
                "mac/debug",
                "windows10/debug",
            },
        }
    )
    test_scheduling.close_failing_together_db("config_group")

    result = testselect.select_configs(
        {"group1": 0.0},
        1.0,
    )
    assert len(result) == 1
    assert set(result["group1"]) == {"linux2404-64-asan/debug", "linux2404-64/opt"}

    result = testselect.select_configs(
        {"group2": 0.0},
        1.0,
    )
    assert len(result) == 1
    assert set(result["group2"]) == {"linux2404-64/debug", "linux2404-64/opt"}

    result = testselect.select_configs(
        {"group3": 0.0},
        1.0,
    )
    assert len(result) == 1
    assert set(result["group3"]) == {"windows10/debug", "linux2404-64/opt"}

    result = testselect.select_configs(
        {"group1": 0.0, "group2": 0.0},
        1.0,
    )
    assert len(result) == 2
    assert set(result["group1"]) == {"linux2404-64/opt", "linux2404-64-asan/debug"}
    assert set(result["group2"]) == {
        "linux2404-64/opt",
        "linux2404-64/debug",
    }

    result = testselect.select_configs(
        {"group1": 0.0, "group3": 0.0},
        1.0,
    )
    assert len(result) == 2
    assert set(result["group1"]) == {"linux2404-64/opt", "linux2404-64-asan/debug"}
    assert set(result["group3"]) == {"windows10/debug", "linux2404-64/opt"}

    result = testselect.select_configs(
        {"group2": 0.0, "group3": 0.0},
        1.0,
    )
    assert len(result) == 2
    assert set(result["group2"]) == {"linux2404-64/opt", "linux2404-64/debug"}
    assert set(result["group3"]) == {"linux2404-64/opt", "windows10/debug"}

    result = testselect.select_configs(
        {"group1": 0.0, "group2": 0.0, "group3": 0.0},
        1.0,
    )
    assert len(result) == 3
    assert set(result["group1"]) == {"linux2404-64/opt", "linux2404-64-asan/debug"}
    assert set(result["group2"]) == {
        "linux2404-64/opt",
        "windows10/debug",
        "linux2404-64-asan/debug",
    }
    assert set(result["group3"]) == {"linux2404-64/opt", "windows10/debug"}

    # A group selected with >= 0.99 confidence runs on all its configs.
    all_configs = {
        "linux2404-64-asan/debug",
        "linux2404-64/debug",
        "linux2404-64/opt",
        "mac/debug",
        "windows10/debug",
    }
    result = testselect.select_configs(
        {"group1": 0.99, "group2": 0.0},
        1.0,
    )
    assert len(result) == 2
    assert set(result["group1"]) == all_configs
    assert set(result["group2"]) == {"linux2404-64/opt", "linux2404-64/debug"}


def test_eval_apply_transforms_cap() -> None:
    push = {"all_possibly_selected": {"a": 0.9, "b": 0.8, "c": 0.7, "d": 0.4}}
    selected, _ = testselect.eval_apply_transforms("group", push, 0.5, None, 2, None)
    assert selected == {"a", "b"}
    selected, _ = testselect.eval_apply_transforms("group", push, 0.5, None, None, 4)
    assert selected == {"a", "b", "c", "d"}


def test_group_model_xgboost_params() -> None:
    params = testselect.TestGroupSelectModel().clf.named_steps["estimator"].get_params()
    assert (params["n_estimators"], params["learning_rate"], params["max_depth"]) == (
        400,
        0.03,
        4,
    )
    default = testselect.TestLabelSelectModel().clf.named_steps["estimator"]
    assert default.get_params()["n_estimators"] is None


def test_class_balance_weights() -> None:
    y = np.array([1, 0, 0, 0, 1, 0])
    assert list(testselect.class_balance_weights(y)) == [1.0, 0.5, 0.5, 0.5, 1.0, 0.5]
    counted = np.array([True, True, True, False, True, False])
    assert list(testselect.class_balance_weights(y, counted)) == [
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
    ]


def test_group_model_balances_with_weights() -> None:
    import pandas as pd

    model = testselect.TestGroupSelectModel()
    assert "sampler" not in model.clf.named_steps
    assert "sampler" in testselect.TestLabelSelectModel().clf.named_steps
    assert (
        testselect.TestLabelSelectModel().get_sample_weights(np.array([1, 0])) is None
    )

    rng = np.random.default_rng(0)
    y = (rng.random(200) < 0.2).astype(int)
    X = pd.DataFrame(
        {"data": [{"total": float(label * 3 + rng.random())} for label in y]}
    )
    model.row_push_failures = list(rng.integers(1, 20, size=len(y)))
    model.row_push_index = list(range(len(y)))
    model.fit_classifier(X, y)
    probs = model.clf.predict_proba(X)[:, 1]
    assert probs[y == 1].mean() > 0.5 > probs[y == 0].mean()
    assert "row_push_failures" not in model.__getstate__()
    assert "row_push_index" not in model.__getstate__()


def test_items_gen_samples_negatives(monkeypatch) -> None:
    history = [
        ((f"rev{i}",), [{"name": f"group{j}"} for j in range(100)]) for i in range(50)
    ]
    classes = {
        (revs[0], test_data["name"]): int(test_data["name"] == "group0")
        for revs, test_datas in history
        for test_data in test_datas
    }
    monkeypatch.setattr(
        testselect.test_scheduling,
        "get_test_scheduling_history",
        lambda granularity: iter(history),
    )
    monkeypatch.setattr(
        testselect, "get_commit_map", lambda: {f"rev{i}": {} for i in range(50)}
    )
    monkeypatch.setattr(testselect.commit_features, "merge_commits", lambda commits: {})

    model = testselect.TestGroupSelectModel()
    labels = [label for _, label in model.items_gen(classes)]
    # All the positives, and about 2% of the negatives.
    assert sum(labels) == 50
    assert 50 < len(labels) < 250
    # The same rows are generated every time.
    assert labels == [label for _, label in model.items_gen(classes)]


def test_positive_weights() -> None:
    y = np.array([1, 1, 0, 1])
    push_failures = np.array([1, 10, 10, 5])
    assert list(testselect.positive_weights(y, push_failures, 5)) == [
        1.0,
        0.5,
        1.0,
        1.0,
    ]


def test_group_model_sample_weights() -> None:
    model = testselect.TestGroupSelectModel()
    assert model.positive_weight_k == 5
    y = np.array([1, 0, 1, 0, 0, 0])
    model.row_push_failures = [10, 10, 1, 1, 1, 1]
    model.row_push_index = [0] * 6
    assert list(model.get_sample_weights(y)) == [0.5, 0.5, 1.0, 0.5, 0.5, 0.5]


def test_group_model_uses_manifest_suite() -> None:
    from bugbug import test_scheduling_features

    extractors = (
        testselect.TestGroupSelectModel()
        .extraction_pipeline.steps[0][1]
        .feature_extractors
    )
    assert any(
        isinstance(fe, test_scheduling_features.ManifestSuite) for fe in extractors
    )
    assert any(
        isinstance(fe, test_scheduling_features.TouchedGroupDirs) for fe in extractors
    )


def test_recency_weights() -> None:
    weights = testselect.recency_weights(np.array([0, 10, 20]), 10)
    assert list(weights) == [0.25, 0.5, 1.0]


def test_compute_confidence_thresholds() -> None:
    push_confidences = [
        [0.9, 0.75, 0.5, 0.2],
        [0.8, 0.6, 0.3],
        [0.95, 0.4],
    ]
    # 1 runnable per push on average: the 3 highest confidences are 0.95, 0.9, 0.8.
    assert testselect.compute_confidence_thresholds(
        push_confidences, {"high": 1, "low": 2}
    ) == {"high": 0.8, "low": 0.5}
    # Thresholds are rounded down to two decimals, like the confidences.
    assert testselect.compute_confidence_thresholds([[0.456]], {"high": 1}) == {
        "high": 0.45
    }
    # Targets larger than the number of runnables use the lowest confidence.
    assert testselect.compute_confidence_thresholds([[0.7, 0.3]], {"low": 5}) == {
        "low": 0.3
    }


def test_confidence_thresholds_default() -> None:
    assert testselect.TestGroupSelectModel().confidence_thresholds is None
    assert set(testselect.CONFIDENCE_LEVEL_TARGETS) == {"label", "group"}


def test_share_caught() -> None:
    pushes = [
        {"failures": ["a"], "all_possibly_selected": {"a": 0.6, "b": 0.9}},
        {"failures": ["c", "d"], "all_possibly_selected": {"d": 0.4}},
        {"failures": ["e"], "all_possibly_selected": {}},
        {"failures": [], "all_possibly_selected": {"f": 0.9}},
    ]
    assert testselect.share_caught(pushes, 0.5) == 1 / 3
    assert testselect.share_caught(pushes, 0.3) == 2 / 3
    assert testselect.share_caught(pushes[3:], 0.5) is None
    assert set(testselect.CONFIDENCE_LEVEL_MIN_CAUGHT) == set(
        testselect.CONFIDENCE_LEVEL_TARGETS
    )
