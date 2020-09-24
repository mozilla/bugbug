# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import db, test_scheduling


def count(is_first_task, is_second_task):
    assert db.download(test_scheduling.PUSH_DATA_LABEL_DB)

    push_data = list(db.read(test_scheduling.PUSH_DATA_LABEL_DB))

    print(f"Analyzing {len(push_data)} pushes...")

    all_tasks = set(task for _, _, push_tasks, _, _ in push_data for task in push_tasks)

    print(f"Considering {len(all_tasks)} tasks...")

    count_runs = 0
    count_any_of_the_two = 0
    count_first_but_not_second = 0
    count_second_but_not_first = 0

    for push in push_data:
        (
            revisions,
            fix_revision,
            push_tasks,
            possible_regressions,
            likely_regressions,
        ) = push

        first_group_tasks = [
            task.split("/")[1] for task in push_tasks if is_first_task(task)
        ]
        second_group_tasks = [
            task.split("/")[1] for task in push_tasks if is_second_task(task)
        ]

        if len(first_group_tasks) == 0 and len(second_group_tasks) == 0:
            continue

        in_both_tasks = set(first_group_tasks) & set(second_group_tasks)

        # Only consider pushes where tasks run in both groups.
        if len(in_both_tasks) == 0:
            continue

        count_runs += 1

        failures = [
            task
            for task in likely_regressions + possible_regressions
            if any(task.endswith(in_both_task) for in_both_task in in_both_tasks)
        ]

        first_failures = [task for task in failures if is_first_task(task)]
        second_failures = [task for task in failures if is_second_task(task)]

        if len(first_failures) > 0 or len(second_failures) > 0:
            count_any_of_the_two += 1

        if len(first_failures) > 0 and len(second_failures) == 0:
            count_first_but_not_second += 1
        elif len(first_failures) == 0 and len(second_failures) > 0:
            count_second_but_not_first += 1

    return (
        count_runs,
        count_any_of_the_two,
        count_first_but_not_second,
        count_second_but_not_first,
    )


def main():
    platform_part_groups1 = [["mingw", "32"]]
    platform_part_groups2 = [["mingw", "64"], ["win", "64"], ["win", "32"]]

    def check(task, platform_part_groups):
        return "/" in task and any(
            all(platform_part in task.split("/")[0] for platform_part in platform_parts)
            for platform_parts in platform_part_groups
        )

    def is_first_task(task):
        return check(task, platform_part_groups1)

    def is_second_task(task):
        return check(task, platform_part_groups2)

    (
        count_runs,
        count_any_of_the_two,
        count_first_but_not_second,
        count_second_but_not_first,
    ) = count(is_first_task, is_second_task)
    print(
        f"Out of {count_runs} runs, any of the two failed {count_any_of_the_two} times. The first exclusively failed {count_first_but_not_second} times, the second exclusively failed {count_second_but_not_first} times."
    )


if __name__ == "__main__":
    main()
