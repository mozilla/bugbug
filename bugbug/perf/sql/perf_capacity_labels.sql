-- Each performance task label with the worker pool it ran on, its runs there
-- and the median run time, for the project's tasks submitted in
-- [date_from, date_to). Runs against the fxci dataset in BigQuery.
--
-- Columns (one row per label and pool):
--   label        text   task label
--   pool         text   worker pool (task_queue_id)
--   runs         int    runs that started and finished
--   p50_seconds  int?   median run time in seconds
WITH perf_tasks AS (
  SELECT task_id, submission_date, tags.label AS label, task_queue_id
  FROM `moz-fx-data-shared-prod.fxci.tasks`
  WHERE submission_date >= "{date_from}"
    AND submission_date < "{date_to}"
    AND {perf_task_filter}
),
runs AS (
  SELECT task_id, submission_date,
    TIMESTAMP_DIFF(resolved, started, SECOND) AS seconds
  FROM `moz-fx-data-shared-prod.fxci.task_runs`
  WHERE submission_date >= "{date_from}"
    AND submission_date < "{date_to}"
    AND started IS NOT NULL
    AND resolved IS NOT NULL
)
SELECT t.label, t.task_queue_id AS pool, COUNT(*) AS runs,
  APPROX_QUANTILES(r.seconds, 100)[OFFSET(50)] AS p50_seconds
FROM perf_tasks t JOIN runs r USING (task_id, submission_date)
GROUP BY 1, 2
