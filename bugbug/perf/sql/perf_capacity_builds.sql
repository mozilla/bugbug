-- Of the pushes without a full performance suite (fewer than min_labels perf
-- tasks), how many carried each listed shippable build, over tasks submitted
-- in [date_from, date_to). A push is a decision task's group.
--
-- Columns (one row per build label):
--   label       text  build label
--   with_build  int   partial pushes that ran the build
--   pushes      int   partial pushes in all
WITH decision_groups AS (
  SELECT task_id AS group_id
  FROM `moz-fx-data-shared-prod.fxci.tasks`
  WHERE submission_date >= "{date_from}"
    AND submission_date < "{date_to}"
    AND tags.project = '{repository}'
    AND tags.kind = 'decision-task'
),
perf_counts AS (
  SELECT task_group_id AS group_id, COUNT(*) AS perf_tasks
  FROM `moz-fx-data-shared-prod.fxci.tasks`
  WHERE submission_date >= "{date_from}"
    AND submission_date < "{date_to}"
    AND {perf_task_filter}
  GROUP BY 1
),
partial AS (
  SELECT g.group_id
  FROM decision_groups g LEFT JOIN perf_counts p USING (group_id)
  WHERE COALESCE(p.perf_tasks, 0) < {min_labels}
),
builds AS (
  SELECT DISTINCT task_group_id AS group_id, tags.label AS label
  FROM `moz-fx-data-shared-prod.fxci.tasks`
  WHERE submission_date >= "{date_from}"
    AND submission_date < "{date_to}"
    AND tags.project = '{repository}'
    AND tags.kind = 'build'
    AND tags.label IN ({build_labels})
)
SELECT b.label, COUNT(DISTINCT b.group_id) AS with_build,
  (SELECT COUNT(*) FROM partial) AS pushes
FROM partial JOIN builds b USING (group_id)
GROUP BY 1
