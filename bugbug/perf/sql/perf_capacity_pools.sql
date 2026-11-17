-- Each worker pool's runs of the project's performance tasks submitted in
-- [date_from, date_to), with the observed queue waits and run times.
--
-- Columns (one row per pool):
--   pool      text    worker pool (task_queue_id)
--   runs      int     runs that started and finished
--   wait_p50  float?  median minutes from scheduled to started
--   wait_p90  float?  90th percentile of the same
--   run_p50   float?  median minutes from started to resolved
--   run_p90   float?  90th percentile of the same
WITH perf_tasks AS (
  SELECT task_id, submission_date, task_queue_id
  FROM `moz-fx-data-shared-prod.fxci.tasks`
  WHERE submission_date >= "{date_from}"
    AND submission_date < "{date_to}"
    AND {perf_task_filter}
),
runs AS (
  SELECT task_id, submission_date,
    TIMESTAMP_DIFF(resolved, started, SECOND) AS seconds,
    TIMESTAMP_DIFF(started, scheduled, SECOND) AS wait
  FROM `moz-fx-data-shared-prod.fxci.task_runs`
  WHERE submission_date >= "{date_from}"
    AND submission_date < "{date_to}"
    AND started IS NOT NULL
    AND resolved IS NOT NULL
)
SELECT t.task_queue_id AS pool, COUNT(*) AS runs,
  APPROX_QUANTILES(r.wait, 100)[OFFSET(50)] / 60 AS wait_p50,
  APPROX_QUANTILES(r.wait, 100)[OFFSET(90)] / 60 AS wait_p90,
  APPROX_QUANTILES(r.seconds, 100)[OFFSET(50)] / 60 AS run_p50,
  APPROX_QUANTILES(r.seconds, 100)[OFFSET(90)] / 60 AS run_p90
FROM perf_tasks t JOIN runs r USING (task_id, submission_date)
GROUP BY 1
