# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import collections
import concurrent.futures
import logging
import math
import pickle
import statistics
from functools import reduce
from typing import (
    Any,
    Callable,
    Collection,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)

import numpy as np
import xgboost
from imblearn.under_sampling import RandomUnderSampler
from ortools.linear_solver import pywraplp
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline
from tqdm import tqdm

from bugbug import (
    commit_features,
    repository,
    test_scheduling,
    test_scheduling_features,
    utils,
)
from bugbug.model import Model

logger = logging.getLogger(__name__)


def get_commit_map(
    revs: Optional[Set[test_scheduling.Revision]] = None,
) -> Dict[test_scheduling.Revision, repository.CommitDict]:
    commit_map = {}

    for commit in repository.get_commits():
        if revs is not None and commit["node"] not in revs:
            continue

        commit_map[commit["node"]] = commit

    assert len(commit_map) > 0
    return commit_map


class TestSelectModel(Model):
    def __init__(self, lemmatization=False, granularity="label", failures_skip=None):
        Model.__init__(self, lemmatization)

        self.granularity = granularity
        self.failures_skip = failures_skip

        self.training_dbs = [repository.COMMITS_DB]
        self.eval_dbs[repository.COMMITS_DB] = (
            repository.COMMITS_DB,
            repository.COMMIT_EXPERIENCES_DB,
        )
        if granularity == "label":
            self.training_dbs.append(test_scheduling.TEST_LABEL_SCHEDULING_DB)
            self.eval_dbs[test_scheduling.TEST_LABEL_SCHEDULING_DB] = (
                test_scheduling.PAST_FAILURES_LABEL_DB,
                test_scheduling.FAILING_TOGETHER_LABEL_DB,
            )
        elif granularity == "group":
            self.training_dbs.append(test_scheduling.TEST_GROUP_SCHEDULING_DB)
            self.eval_dbs[test_scheduling.TEST_GROUP_SCHEDULING_DB] = (
                test_scheduling.PAST_FAILURES_GROUP_DB,
                test_scheduling.TOUCHED_TOGETHER_DB,
            )
            self.eval_dbs[test_scheduling.TEST_CONFIG_GROUP_SCHEDULING_DB] = (
                test_scheduling.FAILING_TOGETHER_CONFIG_GROUP_DB,
            )
        elif granularity == "config_group":
            self.training_dbs.append(test_scheduling.TEST_CONFIG_GROUP_SCHEDULING_DB)
            self.eval_dbs[test_scheduling.TEST_CONFIG_GROUP_SCHEDULING_DB] = (
                test_scheduling.PAST_FAILURES_CONFIG_GROUP_DB,
                test_scheduling.TOUCHED_TOGETHER_DB,
            )

        self.cross_validation_enabled = False

        self.entire_dataset_training = True

        self.sampler = RandomUnderSampler(random_state=0)

        feature_extractors = [
            test_scheduling_features.prev_failures(),
        ]

        if granularity == "label":
            feature_extractors += [
                test_scheduling_features.platform(),
                # test_scheduling_features.chunk(),
                test_scheduling_features.suite(),
            ]
        elif granularity in ("group", "config_group"):
            feature_extractors += [
                test_scheduling_features.path_distance(),
                test_scheduling_features.common_path_components(),
                test_scheduling_features.touched_together(),
            ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "commit_extractor",
                    commit_features.CommitExtractor(feature_extractors, []),
                ),
                ("union", ColumnTransformer([("data", DictVectorizer(), "data")])),
            ]
        )

        self.clf = xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count())
        self.clf.set_params(predictor="cpu_predictor")

    def get_pushes(
        self, apply_filters: bool = False
    ) -> Tuple[List[Dict[str, Any]], int]:
        pushes = []
        for revs, test_datas in test_scheduling.get_test_scheduling_history(
            self.granularity
        ):
            failures = []
            passes = []

            for test_data in test_datas:
                name = test_data["name"]

                if (
                    test_data["is_likely_regression"]
                    or test_data["is_possible_regression"]
                ):
                    failures.append(name)
                else:
                    passes.append(name)

            if apply_filters:
                if self.failures_skip and len(failures) > self.failures_skip:
                    continue

            pushes.append(
                {
                    "revs": revs,
                    "failures": failures,
                    "passes": passes,
                }
            )

        return pushes, math.floor(0.9 * len(pushes))

    # To properly test the performance of our model, we need to split the data
    # according to time: we train on older pushes and evaluate on newer pushes.
    def train_test_split(self, X, y):
        pushes, train_push_len = self.get_pushes(True)
        train_len = sum(
            len(push["failures"]) + len(push["passes"])
            for push in pushes[:train_push_len]
        )
        print(
            f"{train_push_len} pushes in the training set (corresponding to {train_len} push/jobs)"
        )
        return X[:train_len], X[train_len:], y[:train_len], y[train_len:]

    def items_gen(self, classes):
        commit_map = get_commit_map()

        for revs, test_datas in test_scheduling.get_test_scheduling_history(
            self.granularity
        ):
            commits = tuple(
                commit_map.pop(revision) for revision in revs if revision in commit_map
            )
            assert len(commits) > 0

            for test_data in test_datas:
                name = test_data["name"]

                if (revs[0], name) not in classes:
                    continue

                commit_data = commit_features.merge_commits(commits)
                commit_data["test_job"] = test_data
                yield commit_data, classes[(revs[0], name)]

    def get_labels(self):
        classes = {}
        pushes, _ = self.get_pushes(True)

        for push in pushes:
            for name in push["failures"]:
                classes[(push["revs"][0], name)] = 1

            for name in push["passes"]:
                classes[(push["revs"][0], name)] = 0

        print("{} pushes considered".format(len(pushes)))
        print(
            "{} pushes with at least one failure".format(
                sum(1 for push in pushes if len(push["failures"]) > 0)
            )
        )
        print(
            "{} push/jobs failed".format(
                sum(1 for label in classes.values() if label == 1)
            )
        )
        print(
            "{} push/jobs did not fail".format(
                sum(1 for label in classes.values() if label == 0)
            )
        )

        return classes, [0, 1]

    def select_tests(
        self,
        commits: Sequence[repository.CommitDict],
        confidence: float = 0.5,
        push_num: Optional[int] = None,
    ) -> Dict[str, float]:
        commit_data = commit_features.merge_commits(commits)

        past_failures_data = test_scheduling.get_past_failures(self.granularity, True)

        if push_num is None:
            push_num = past_failures_data["push_num"] + 1
        all_runnables = past_failures_data["all_runnables"]

        commit_tests = []
        for data in test_scheduling.generate_data(
            self.granularity,
            past_failures_data,
            commit_data,
            push_num,
            all_runnables,
            tuple(),
            tuple(),
        ):
            commit_test = commit_data.copy()
            commit_test["test_job"] = data
            commit_tests.append(commit_test)

        probs = self.classify(commit_tests, probabilities=True)
        selected_indexes = np.argwhere(probs[:, 1] >= confidence)[:, 0]
        return {
            commit_tests[i]["test_job"]["name"]: math.floor(probs[i, 1] * 100) / 100
            for i in selected_indexes
        }

    def _get_cost(self, config: str) -> int:
        costs = [
            (("build", "opt"), 1),
            (("build", "debug"), 2),
            (("linux1804-64", "opt"), 2),
            (("linux1804-64", "debug"), 3),
            (("windows10", "opt"), 4),
            (("windows10", "debug"), 5),
            (("android-em", "opt"), 6),
            (("android-em", "debug"), 7),
            (("windows7", "opt"), 8),
            (("windows7", "debug"), 9),
            (("mac", "opt"), 10),
            (("mac", "debug"), 11),
            (("asan", "opt"), 12),
            (("asan", "debug"), 13),
            (("linux1804-32", "opt"), 14),
            (("linux1804-32", "debug"), 15),
            (("android-hw", "opt"), 16),
            (("android-hw", "debug"), 17),
            (("tsan", "opt"), 18),
            (("tsan", "debug"), 19),
            (("test-linux1804-64-shippable/opt-*-e10s",), 1),
        ]

        for substrings, cost in reversed(costs):
            if all(s in config for s in substrings):
                return cost

        raise Exception(f"Couldn't find cost for {config}")

    def _generate_equivalence_sets(
        self,
        tasks: Iterable[str],
        min_redundancy_confidence: float,
        load_failing_together: Callable[[str], Dict[str, Tuple[float, float]]],
        assume_redundant: bool,
    ) -> List[Set[str]]:
        # Generate 'equivalence sets', containing all tasks that are redundant with
        # each other.
        groups: List[Set[str]] = []
        task_to_groups: Dict[str, Set[int]] = collections.defaultdict(set)
        incompatible_groups: Dict[str, Set[int]] = collections.defaultdict(set)

        def create_group(task: str) -> None:
            if task in task_to_groups:
                return

            groups.append({task})
            task_to_groups[task] = {len(groups) - 1}

        # Add task1 to all equivalence groups where task2 is present, and likewise for task2.
        # Skip groups which contain tasks that are not redundant with task1.
        def add_to_groups(task1: str, task2: str) -> None:
            found = False

            if task1 in task_to_groups:
                for i in task_to_groups[task1]:
                    if task2 in incompatible_groups and i in incompatible_groups[task2]:
                        continue

                    groups[i].add(task2)
                    task_to_groups[task2].add(i)
                    found = True

            if task2 in task_to_groups:
                for i in task_to_groups[task2]:
                    if task1 in incompatible_groups and i in incompatible_groups[task1]:
                        continue

                    groups[i].add(task1)
                    task_to_groups[task1].add(i)
                    found = True

            # No suitable equivalence group was found for the tasks, create a new one.
            if found:
                return

            group = {task1, task2}
            groups.append(group)
            task_to_groups[task1].add(len(groups) - 1)
            task_to_groups[task2].add(len(groups) - 1)

        def mark_incompatible(task1: str, task2: str) -> None:
            if task1 in task_to_groups:
                incompatible_groups[task2].update(task_to_groups[task1])

            if task2 in task_to_groups:
                incompatible_groups[task1].update(task_to_groups[task2])

        sorted_tasks = sorted(tasks)
        for i, task1 in enumerate(sorted_tasks):
            create_group(task1)

            try:
                failing_together_stats = load_failing_together(task1)
            except KeyError:
                failing_together_stats = {}

            for task2 in sorted_tasks[i + 1 :]:
                try:
                    support, confidence = failing_together_stats[task2]
                except KeyError:
                    if not assume_redundant:
                        confidence = 0.0
                    else:
                        confidence = 1.0

                if confidence >= min_redundancy_confidence:
                    add_to_groups(task1, task2)
                else:
                    mark_incompatible(task1, task2)

        return groups

    def _get_equivalence_sets(self, min_redundancy_confidence: float):
        try:
            with open(
                f"equivalence_sets_{min_redundancy_confidence}.pickle", "rb"
            ) as f:
                return pickle.load(f)
        except FileNotFoundError:
            past_failures_data = test_scheduling.get_past_failures(
                self.granularity, True
            )
            all_runnables = past_failures_data["all_runnables"]

            equivalence_sets = {}
            failing_together = test_scheduling.get_failing_together_db(
                "config_group", True
            )
            all_configs = pickle.loads(failing_together[b"$ALL_CONFIGS$"])
            configs_by_group = pickle.loads(failing_together[b"$CONFIGS_BY_GROUP$"])
            for group in all_runnables:
                key = test_scheduling.failing_together_key(group)
                try:
                    failing_together_stats = pickle.loads(failing_together[key])
                except KeyError:
                    failing_together_stats = {}

                def load_failing_together(
                    config: str,
                ) -> Dict[str, Tuple[float, float]]:
                    return failing_together_stats[config]

                configs = (
                    configs_by_group[group]
                    if group in configs_by_group
                    else all_configs
                )

                equivalence_sets[group] = self._generate_equivalence_sets(
                    configs, min_redundancy_confidence, load_failing_together, True
                )

            with open(
                f"equivalence_sets_{min_redundancy_confidence}.pickle", "wb"
            ) as f:
                pickle.dump(equivalence_sets, f)

            return equivalence_sets

    def _solve_optimization(self, solver: pywraplp.Solver) -> bool:
        # The MIP solver is usually fast (milliseconds). If we hit a weird problem,
        # accept a suboptimal solution after 10 seconds.
        solver.SetTimeLimit(10000)
        status = solver.Solve()

        if status == pywraplp.Solver.INFEASIBLE:
            logger.warning("Optimization problem is infeasible")
            return False
        elif status == pywraplp.Solver.NOT_SOLVED:
            logger.warning("Optimization problem could not be solved in time")
            return False

        return True

    def reduce(
        self,
        tasks: Collection[str],
        min_redundancy_confidence: float,
        assume_redundant: bool = False,
    ) -> Set[str]:
        failing_together = test_scheduling.get_failing_together_db(
            self.granularity, True
        )

        def load_failing_together(task: str) -> Dict[str, Tuple[float, float]]:
            key = test_scheduling.failing_together_key(task)
            return pickle.loads(failing_together[key])

        solver = pywraplp.Solver(
            "select_configs", pywraplp.Solver.CBC_MIXED_INTEGER_PROGRAMMING
        )

        task_vars = {task: solver.BoolVar(task) for task in tasks}

        equivalence_sets = self._generate_equivalence_sets(
            tasks, min_redundancy_confidence, load_failing_together, assume_redundant
        )

        # Create constraints to ensure at least one task from each set of equivalent
        # sets is selected.

        mutually_exclusive = True
        seen = set()
        for equivalence_set in equivalence_sets:
            if any(config in seen for config in equivalence_set):
                mutually_exclusive = False
                break

            seen |= equivalence_set

        for equivalence_set in equivalence_sets:
            sum_constraint = sum(task_vars[task] for task in equivalence_set)
            if mutually_exclusive:
                solver.Add(sum_constraint == 1)
            else:
                solver.Add(sum_constraint >= 1)

        # Choose the best set of tasks that satisfy the constraints with the lowest cost.
        solver.Minimize(
            sum(self._get_cost(task) * task_vars[task] for task in task_vars.keys())
        )

        if self._solve_optimization(solver):
            return {
                task
                for task, task_var in task_vars.items()
                if task_var.solution_value() == 1
            }
        else:
            return set(tasks)

    def select_configs(
        self,
        groups: Collection[str],
        min_redundancy_confidence: float,
        max_configurations: int = 3,
    ) -> Dict[str, List[str]]:
        failing_together = test_scheduling.get_failing_together_db("config_group", True)

        all_configs = pickle.loads(failing_together[b"$ALL_CONFIGS$"])
        all_configs_by_group = pickle.loads(failing_together[b"$CONFIGS_BY_GROUP$"])
        config_costs = {config: self._get_cost(config) for config in all_configs}

        solver = pywraplp.Solver(
            "select_configs", pywraplp.Solver.CBC_MIXED_INTEGER_PROGRAMMING
        )

        config_vars = {config: solver.BoolVar(config) for config in all_configs}
        config_group_vars = {
            (config, group): solver.BoolVar(f"{group}@{config}")
            for group in groups
            for config in (
                all_configs_by_group[group]
                if group in all_configs_by_group
                else all_configs
            )
        }

        equivalence_sets = self._get_equivalence_sets(min_redundancy_confidence)

        for group in groups:
            # Create constraints to ensure at least one task from each set of equivalent
            # groups is selected.

            mutually_exclusive = True
            seen = set()
            for equivalence_set in equivalence_sets[group]:
                if any(config in seen for config in equivalence_set):
                    mutually_exclusive = False
                    break

                seen |= equivalence_set

            set_variables = [
                solver.BoolVar(f"{group}_{j}")
                for j in range(len(equivalence_sets[group]))
            ]

            for j, equivalence_set in enumerate(equivalence_sets[group]):
                set_variable = set_variables[j]

                sum_constraint = sum(
                    config_group_vars[(config, group)] for config in equivalence_set
                )
                if mutually_exclusive:
                    solver.Add(sum_constraint == set_variable)
                else:
                    solver.Add(sum_constraint >= set_variable)

            # Cap to max_configurations equivalence sets.
            solver.Add(
                sum(set_variables)
                >= (
                    max_configurations
                    if len(set_variables) >= max_configurations
                    else len(set_variables)
                )
            )

        for config in all_configs:
            solver.Add(
                sum(
                    config_group_var
                    for (c, g), config_group_var in config_group_vars.items()
                    if config == c
                )
                <= config_vars[config] * len(groups)
            )

        # Choose the best set of tasks that satisfy the constraints with the lowest cost.
        # The cost is calculated as a sum of the following:
        # - a fixed cost to use a config (since selecting a config has overhead, it is
        #   wasteful to select a config only to run a single group);
        # - a cost for each selected group.
        # This way, for example, if we have a group that must run on a costly config and a
        # group that can run either on the costly one or on a cheaper one, they'd both run
        # on the costly one (since we have to pay its setup cost anyway).
        solver.Minimize(
            sum(10 * config_costs[c] * config_vars[c] for c in config_vars.keys())
            + sum(
                config_costs[config] * config_group_vars[(config, group)]
                for config, group in config_group_vars.keys()
            )
        )

        configs_by_group: Dict[str, List[str]] = {}
        for group in groups:
            configs_by_group[group] = []

        if self._solve_optimization(solver):
            for (config, group), config_group_var in config_group_vars.items():
                if config_group_var.solution_value() == 1:
                    configs_by_group[group].append(config)
        else:
            least_cost_config = min(config_costs, key=lambda c: config_costs[c])
            for group in groups:
                configs_by_group[group].append(least_cost_config)

        return configs_by_group

    def evaluation(self) -> None:
        # Get a test set of pushes on which to test the model.
        pushes, train_push_len = self.get_pushes(False)

        # To evaluate the model with reductions enabled, we need to regenerate the failing together DB, using
        # only failure data from the training pushes (otherwise, we'd leak training information into the test
        # set).
        print("Generate failing together DB (restricted to training pushes)")
        push_data_iter, push_data_count, _ = test_scheduling.get_push_data(
            "label" if self.granularity == "label" else "config_group"
        )
        test_scheduling.generate_failing_together_probabilities(
            "label" if self.granularity == "label" else "config_group",
            push_data_iter(),
            push_data_count,
            pushes[train_push_len - 1]["revs"][0],
        )

        test_pushes_list = pushes[train_push_len:]

        all_tasks = reduce(
            lambda x, y: x | y,
            (
                set(push["failures"]) | set(push["passes"])
                for push in test_pushes_list[-28:]
            ),
        )

        all_revs = set(sum((push["revs"] for push in test_pushes_list), []))

        test_pushes_failures = sum(
            1 for push in test_pushes_list if len(push["failures"]) > 0
        )

        test_pushes = {push["revs"][0]: push for push in test_pushes_list}

        if self.granularity == "group":
            for (
                revisions,
                fix_revision,
                push_runnables,
                possible_regressions,
                likely_regressions,
            ) in tqdm(push_data_iter(), total=push_data_count):
                if revisions[0] not in test_pushes:
                    continue

                test_pushes[revisions[0]]["config_group_failures"] = (
                    possible_regressions + likely_regressions
                )

        print(
            f"Testing on {len(test_pushes)} ({test_pushes_failures} with failures) out of {len(pushes)}. {len(all_tasks)} schedulable tasks."
        )

        del pushes

        commit_map = get_commit_map(all_revs)

        past_failures_data = test_scheduling.get_past_failures(self.granularity, True)
        last_push_num = past_failures_data["push_num"]
        past_failures_data.close()

        # Select tests for all the pushes in the test set.
        for i, push in enumerate(tqdm(test_pushes.values())):
            commits = tuple(
                commit_map.pop(revision)
                for revision in push["revs"]
                if revision in commit_map
            )
            if len(commits) == 0:
                push["all_possibly_selected"] = {}
                continue

            push_num = last_push_num - (len(test_pushes) - (i + 1))

            # Note: we subtract 100 to the push number to make sure we don't use
            # past failure data for the push itself.
            # The number 100 comes from the fact that in the past failure data
            # generation we store past failures in batches of 100 pushes.
            push["all_possibly_selected"] = self.select_tests(
                commits, 0.5, push_num - 100
            )

        def do_eval(
            executor: concurrent.futures.ProcessPoolExecutor,
            confidence_threshold: float,
            reduction: Optional[float],
            cap: Optional[int],
            minimum: Optional[int],
        ) -> None:
            futures: Dict[concurrent.futures.Future, Dict[str, Any]] = {}
            for push in test_pushes.values():
                futures[
                    executor.submit(
                        eval_apply_transforms,
                        self,
                        push,
                        confidence_threshold,
                        reduction,
                        cap,
                        minimum,
                    )
                ] = push

            for future in concurrent.futures.as_completed(futures):
                exc = future.exception()
                if exc is not None:
                    print(
                        "Exception {} while running {}".format(
                            exc, futures[future]["revs"][0]
                        )
                    )
                    for f in futures:
                        f.cancel()

                push = futures[future]
                selected, group_configs = future.result()

                if reduction is not None and self.granularity == "group":
                    push["number_configs"] = len(
                        set(
                            sum(
                                group_configs.values(),
                                [],
                            )
                        )
                    )
                    selected_config_groups = set(
                        (config, group)
                        for group, configs in group_configs.items()
                        for config in configs
                    )
                    caught_config_groups = selected_config_groups & set(
                        push["config_group_failures"]
                    )
                    push["caught_one_config_group"] = (
                        len(caught_config_groups) > 0
                        if len(push["config_group_failures"]) != 0
                        else None
                    )
                    push["caught_percentage_config_group"] = (
                        len(caught_config_groups) / len(push["config_group_failures"])
                        if len(push["config_group_failures"]) != 0
                        else None
                    )

                caught = selected & set(push["failures"])

                push["number_scheduled"] = len(selected)
                push["caught_one"] = (
                    len(caught) > 0 if len(push["failures"]) != 0 else None
                )
                push["some_didnt_run"] = (
                    not selected.issubset(set(push["passes"]) | set(push["failures"])),
                )
                push["caught_percentage"] = (
                    len(caught) / len(push["failures"])
                    if len(push["failures"]) != 0
                    else None
                )

            min_scheduled = min(
                result["number_scheduled"] for result in test_pushes.values()
            )
            max_scheduled = max(
                result["number_scheduled"] for result in test_pushes.values()
            )
            average_scheduled = statistics.mean(
                result["number_scheduled"] for result in test_pushes.values()
            )
            num_failing_pushes = sum(
                1 for result in test_pushes.values() if result["caught_one"] is not None
            )
            num_caught_one = sum(
                1 for result in test_pushes.values() if result["caught_one"]
            )
            num_caught_one_or_some_didnt_run = sum(
                1
                for result in test_pushes.values()
                if result["caught_one"]
                or (result["caught_one"] is not None and result["some_didnt_run"])
            )
            percentage_caught_one = 100 * num_caught_one / num_failing_pushes
            percentage_caught_one_or_some_didnt_run = (
                100 * num_caught_one_or_some_didnt_run / num_failing_pushes
            )
            average_caught_percentage = 100 * statistics.mean(
                result["caught_percentage"]
                for result in test_pushes.values()
                if result["caught_percentage"] is not None
            )

            reduction_str = (
                f"enabled at {reduction * 100}%"
                if reduction is not None
                else "disabled"
            )

            message = f"For confidence threshold {confidence_threshold}, with reduction {reduction_str}, cap at {cap}, and minimum at {minimum}: scheduled {average_scheduled} tasks on average (min {min_scheduled}, max {max_scheduled}). In {percentage_caught_one}% of pushes we caught at least one failure ({percentage_caught_one_or_some_didnt_run}% ignoring misses when some of our selected tasks didn't run). On average, we caught {average_caught_percentage}% of all seen failures."

            if reduction is not None and self.granularity == "group":
                average_configs = statistics.mean(
                    result["number_configs"] for result in test_pushes.values()
                )
                median_configs = statistics.median(
                    result["number_configs"] for result in test_pushes.values()
                )
                message += f" On average, we selected {average_configs} configs (a median of {median_configs} configs)."

                num_caught_one_config_group = sum(
                    1
                    for result in test_pushes.values()
                    if result["caught_one_config_group"]
                )
                percentage_caught_one_config_group = (
                    100 * num_caught_one_config_group / num_failing_pushes
                )
                average_caught_percentage_config_group = 100 * statistics.mean(
                    result["caught_percentage_config_group"]
                    for result in test_pushes.values()
                    if result["caught_percentage_config_group"] is not None
                )

                message += f" In {percentage_caught_one_config_group}% of pushes we caught at least one config/group failure. On average, we caught {average_caught_percentage_config_group}% of all seen config/group failures."

            print(message)

        with concurrent.futures.ProcessPoolExecutor(
            max_workers=utils.get_physical_cpu_count()
        ) as executor:
            scenarios = [
                (None, None, None),
                (10, None, None),
                (None, 300, None),
                (None, None, 0.9),
                (None, None, 1.0),
            ]
            for minimum, cap, reduction in scenarios:
                # Pre-generate equivalence sets, so when we run the config selection in multiple processes
                # we don't risk concurrent writes to the equivalence sets file.
                if reduction is not None and self.granularity == "group":
                    self._get_equivalence_sets(reduction)

                for confidence_threshold in [0.5, 0.7, 0.8, 0.85, 0.9, 0.95]:
                    do_eval(executor, confidence_threshold, reduction, cap, minimum)

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()


class TestLabelSelectModel(TestSelectModel):
    def __init__(self, lemmatization=False):
        TestSelectModel.__init__(self, lemmatization, "label", failures_skip=60)


class TestGroupSelectModel(TestSelectModel):
    def __init__(self, lemmatization=False):
        TestSelectModel.__init__(self, lemmatization, "group")


class TestConfigGroupSelectModel(TestSelectModel):
    def __init__(self, lemmatization=False):
        TestSelectModel.__init__(self, lemmatization, "config_group")


def eval_apply_transforms(model, push, confidence_threshold, reduction, cap, minimum):
    group_configs = None

    selected = set(
        name
        for name, confidence in push["all_possibly_selected"].items()
        if confidence >= confidence_threshold
    )

    if reduction is not None:
        if model.granularity == "label":
            selected = model.reduce(selected, reduction)
        elif model.granularity == "group":
            group_configs = model.select_configs(selected, reduction)

    if minimum is not None and len(selected) < minimum:
        remaining = [
            (name, confidence)
            for name, confidence in push["all_possibly_selected"].items()
            if name not in selected
        ]
        selected.update(
            name
            for name, _ in sorted(remaining, key=lambda x: -x[1])[
                : minimum - len(selected)
            ]
        )

    if cap is not None and len(selected) > cap:
        selected = set(
            sorted(
                (
                    (name, confidence)
                    for name, confidence in push["all_possibly_selected"].items()
                    if name in selected
                ),
                key=lambda x: x[1],
                reverse=True,
            )[:cap]
        )

    return selected, group_configs
