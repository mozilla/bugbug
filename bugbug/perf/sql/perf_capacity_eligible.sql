-- Per listed pool, the median minutes from a push's decision task starting
-- to its performance tasks becoming pending: the build chain, over tasks
-- submitted in [date_from, date_to).
--
-- Columns (one row per pool):
--   pool          text    worker pool (task_queue_id)
--   eligible_p50  float?  median minutes from the decision task to pending
WITH decisions AS (
  SELECT t.task_id AS group_id, r.started AS pushed
  FROM `moz-fx-data-shared-prod.fxci.tasks` t
  JOIN `moz-fx-data-shared-prod.fxci.task_runs` r USING (task_id, submission_date)
  WHERE t.submission_date >= "{date_from}"
    AND t.submission_date < "{date_to}"
    AND t.tags.project = '{repository}'
    AND t.tags.kind = 'decision-task'
    AND r.run_id = 0
    AND r.started IS NOT NULL
),
perf AS (
  SELECT t.task_group_id, t.task_queue_id AS pool, r.scheduled
  FROM `moz-fx-data-shared-prod.fxci.tasks` t
  JOIN `moz-fx-data-shared-prod.fxci.task_runs` r USING (task_id, submission_date)
  WHERE t.submission_date >= "{date_from}"
    AND t.submission_date < "{date_to}"
    AND {perf_task_filter}
    AND t.task_queue_id IN ({pools})
    AND r.run_id = 0
    AND r.reason_created = 'scheduled'
    AND r.scheduled IS NOT NULL
)
SELECT p.pool,
  APPROX_QUANTILES(TIMESTAMP_DIFF(p.scheduled, d.pushed, SECOND), 100)[OFFSET(50)] / 60
    AS eligible_p50
FROM perf p JOIN decisions d ON p.task_group_id = d.group_id
GROUP BY 1
