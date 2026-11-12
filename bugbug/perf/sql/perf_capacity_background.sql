-- The hourly occupancy of each listed pool by every run that is not one of
-- the project's performance tasks, in worker-hours per clock hour, over
-- tasks submitted in [date_from, date_to). A run spanning several hours is
-- split between them.
--
-- Columns (one row per pool and hour):
--   pool  text   worker pool (task_queue_id)
--   hour  int    Unix seconds of the hour
--   busy  float  worker-hours spent in that hour
WITH pool_runs AS (
  SELECT t.task_queue_id AS pool, r.started, r.resolved
  FROM `moz-fx-data-shared-prod.fxci.task_runs` r
  JOIN `moz-fx-data-shared-prod.fxci.tasks` t USING (task_id, submission_date)
  WHERE r.submission_date >= "{date_from}"
    AND r.submission_date < "{date_to}"
    AND t.task_queue_id IN ({pools})
    AND r.started IS NOT NULL
    AND r.resolved IS NOT NULL
    AND NOT {perf_task_filter}
)
SELECT pool, UNIX_SECONDS(hour) AS hour,
  SUM(TIMESTAMP_DIFF(LEAST(resolved, TIMESTAMP_ADD(hour, INTERVAL 1 HOUR)),
                     GREATEST(started, hour), SECOND)) / 3600 AS busy
FROM pool_runs, UNNEST(GENERATE_TIMESTAMP_ARRAY(
  TIMESTAMP_TRUNC(started, HOUR), TIMESTAMP_TRUNC(resolved, HOUR), INTERVAL 1 HOUR)) AS hour
GROUP BY 1, 2
