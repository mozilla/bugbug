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
            "linux1804-64-asan/debug": {
                "linux1804-64/debug": (1.0, 0.0),
                "linux1804-64/opt": (1.0, 0.0),
                "mac/debug": (1.0, 0.0),
                "windows10/debug": (1.0, 0.0),
            },
            "linux1804-64/debug": {
                "linux1804-64/opt": (1.0, 1.0),
                "mac/debug": (1.0, 1.0),
                "windows10/debug": (1.0, 1.0),
            },
            "linux1804-64/opt": {
                "mac/debug": (1.0, 1.0),
                "windows10/debug": (1.0, 1.0),
            },
            "mac/debug": {"windows10/debug": (1.0, 1.0)},
        }
    )
    failing_together_config_group[b"group2"] = pickle.dumps(
        {
            "linux1804-64-asan/debug": {
                "linux1804-64/debug": (1.0, 1.0),
                "linux1804-64/opt": (1.0, 0.0),
                "mac/debug": (1.0, 0.0),
                "windows10/debug": (1.0, 0.0),
            },
            "linux1804-64/debug": {
                "linux1804-64/opt": (1.0, 0.0),
                "mac/debug": (1.0, 0.0),
                "windows10/debug": (1.0, 1.0),
            },
            "linux1804-64/opt": {
                "mac/debug": (1.0, 0.0),
                "windows10/debug": (1.0, 0.0),
            },
            "mac/debug": {"windows10/debug": (1.0, 0.0)},
        }
    )
    failing_together_config_group[b"group3"] = pickle.dumps(
        {
            "linux1804-64-asan/debug": {
                "linux1804-64/debug": (1.0, 1.0),
                "linux1804-64/opt": (1.0, 1.0),
                "mac/debug": (1.0, 1.0),
                "windows10/debug": (1.0, 0.0),
            },
            "linux1804-64/debug": {
                "linux1804-64/opt": (1.0, 1.0),
                "mac/debug": (1.0, 1.0),
                "windows10/debug": (1.0, 0.0),
            },
            "linux1804-64/opt": {
                "mac/debug": (1.0, 1.0),
                "windows10/debug": (1.0, 0.0),
            },
            "mac/debug": {"windows10/debug": (1.0, 1.0)},
        }
    )
    failing_together_config_group[b"$ALL_CONFIGS$"] = pickle.dumps(
        [
            "linux1804-64-asan/debug",
            "linux1804-64/debug",
            "linux1804-64/opt",
            "mac/debug",
            "windows10/debug",
        ]
    )
    failing_together_config_group[b"$CONFIGS_BY_GROUP$"] = pickle.dumps(
        {
            "group1": {
                "linux1804-64-asan/debug",
                "linux1804-64/debug",
                "linux1804-64/opt",
                "mac/debug",
                "windows10/debug",
            },
            "group2": {
                "linux1804-64-asan/debug",
                "linux1804-64/debug",
                "linux1804-64/opt",
                "mac/debug",
                "windows10/debug",
            },
            "group3": {
                "linux1804-64-asan/debug",
                "linux1804-64/debug",
                "linux1804-64/opt",
                "mac/debug",
                "windows10/debug",
            },
        }
    )
    test_scheduling.close_failing_together_db("config_group")

    result = testselect.select_configs(
        {
            "group1",
        },
        1.0,
    )
    assert len(result) == 1
    assert set(result["group1"]) == {"linux1804-64-asan/debug", "linux1804-64/opt"}

    result = testselect.select_configs(
        {
            "group2",
        },
        1.0,
    )
    assert len(result) == 1
    assert set(result["group2"]) == {"linux1804-64/debug", "linux1804-64/opt"}

    result = testselect.select_configs(
        {
            "group3",
        },
        1.0,
    )
    assert len(result) == 1
    assert set(result["group3"]) == {"windows10/debug", "linux1804-64/opt"}

    result = testselect.select_configs(
        {
            "group1",
            "group2",
        },
        1.0,
    )
    assert len(result) == 2
    assert set(result["group1"]) == {"linux1804-64/opt", "linux1804-64-asan/debug"}
    assert set(result["group2"]) == {
        "linux1804-64/opt",
        "linux1804-64/debug",
    }

    result = testselect.select_configs(
        {
            "group1",
            "group3",
        },
        1.0,
    )
    assert len(result) == 2
    assert set(result["group1"]) == {"linux1804-64/opt", "linux1804-64-asan/debug"}
    assert set(result["group3"]) == {"windows10/debug", "linux1804-64/opt"}

    result = testselect.select_configs(
        {
            "group2",
            "group3",
        },
        1.0,
    )
    assert len(result) == 2
    assert set(result["group2"]) == {"linux1804-64/opt", "linux1804-64/debug"}
    assert set(result["group3"]) == {"linux1804-64/opt", "windows10/debug"}

    result = testselect.select_configs(
        {
            "group1",
            "group2",
            "group3",
        },
        1.0,
    )
    assert len(result) == 3
    assert set(result["group1"]) == {"linux1804-64/opt", "linux1804-64-asan/debug"}
    assert set(result["group2"]) == {
        "linux1804-64/opt",
        "windows10/debug",
        "linux1804-64-asan/debug",
    }
    assert set(result["group3"]) == {"linux1804-64/opt", "windows10/debug"}
