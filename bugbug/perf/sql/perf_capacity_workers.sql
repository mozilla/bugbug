-- The distinct workers each of the listed pools used per month, over every
-- run on them of tasks submitted in [date_from, date_to).
--
-- Columns (one row per pool and month):
--   pool     text  worker pool (task_queue_id)
--   month    text  YYYY-MM of the run's start
--   workers  int   distinct worker ids
SELECT t.task_queue_id AS pool, FORMAT_TIMESTAMP('%Y-%m', r.started) AS month,
  COUNT(DISTINCT r.worker_id) AS workers
FROM `moz-fx-data-shared-prod.fxci.task_runs` r
JOIN `moz-fx-data-shared-prod.fxci.tasks` t USING (task_id, submission_date)
WHERE r.submission_date >= "{date_from}"
  AND r.submission_date < "{date_to}"
  AND t.task_queue_id IN ({pools})
  AND r.started IS NOT NULL
  AND r.resolved IS NOT NULL
GROUP BY 1, 2
