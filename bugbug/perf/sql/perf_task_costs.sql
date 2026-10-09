-- One row per performance task, with the worker pool it ran on, its runs and
-- machine time, and the attributed cloud cost when the pool is cloud hosted.
-- Hardware pools (releng-hardware, proj-autophone) carry no cloud billing, so
-- their cost stays NULL and machine time per pool is the cost unit for them.
--
-- Runs against the fxci dataset in BigQuery through the STMO data source.
-- Every table is partitioned by submission_date and filtered on it explicitly.
--
-- Columns (one row per perf task):
--   task_id          text    Taskcluster task id; joins to task_ids in the alerts and pushes queries
--   label            text    task label, e.g. test-linux2404-64-shippable/opt-talos-g1
--   project          text    autoland or mozilla-central
--   test_platform    text?   taskgraph tag, e.g. linux2404-64-shippable/opt
--   test_suite       text?   taskgraph tag, e.g. talos, raptor, awsy
--   task_queue_id    text    worker pool, e.g. releng-hardware/gecko-t-linux-talos-2404
--   submission_date  text    day the task was created, YYYY-MM-DD
--   runs             int?    number of runs that started and finished
--   duration         int?    total machine seconds across those runs
--   cost             float?  attributed dollars; NULL for hardware pools, which have no cloud billing
--   costed_runs      int?    runs that contributed to cost; NULL when cost is NULL
--   cloud_provider   text?   gcp or azure when costed, otherwise NULL
WITH perf_tasks AS (
  SELECT
    task_id,
    tags.label AS label,
    tags.project AS project,
    tags.test_platform AS test_platform,
    tags.test_suite AS test_suite,
    task_queue_id,
    CAST(submission_date AS STRING) AS submission_date
  FROM `moz-fx-data-shared-prod.fxci.tasks`
  WHERE submission_date >= "{date_from}"
    AND submission_date < "{date_to}"
    AND tags.project IN ({repositories})
    AND (
      tags.label LIKE "%-browsertime-%"
      OR tags.label LIKE "%-talos-%"
      OR tags.label LIKE "%-awsy%"
      OR tags.label LIKE "%-raptor-%"
      OR tags.label LIKE "perftest-%"
    )
),
runs AS (
  SELECT
    task_id,
    COUNT(*) AS runs,
    SUM(TIMESTAMP_DIFF(resolved, started, SECOND)) AS duration
  FROM `moz-fx-data-shared-prod.fxci.task_runs`
  WHERE submission_date >= "{date_from}"
    AND submission_date < "{date_to}"
    AND started IS NOT NULL
    AND resolved IS NOT NULL
  GROUP BY task_id
),
costs AS (
  SELECT
    task_id,
    SUM(run_cost) AS cost,
    COUNT(*) AS costed_runs,
    ANY_VALUE(cloud_provider) AS cloud_provider
  FROM `moz-fx-data-shared-prod.fxci.task_run_costs`
  WHERE submission_date >= "{date_from}"
    AND submission_date < "{date_to}"
  GROUP BY task_id
)
SELECT
  t.task_id,
  t.label,
  t.project,
  t.test_platform,
  t.test_suite,
  t.task_queue_id,
  t.submission_date,
  r.runs,
  r.duration,
  c.cost,
  c.costed_runs,
  c.cloud_provider
FROM perf_tasks t
LEFT JOIN runs r USING (task_id)
LEFT JOIN costs c USING (task_id)
ORDER BY t.submission_date, t.task_id
