-- One row per push in the window, with its commits and the performance jobs
-- that ran on it. Aggregates use ASCII record (0x1e) and unit (0x1f)
-- separators so a row stays a flat string regardless of the client.
--
-- Runs against the Treeherder Postgres replica exposed by STMO.
--
-- Columns (one row per push):
--   push_id       int    Treeherder push id, global across repositories
--   repository    text   autoland or mozilla-central
--   revision      text   head changeset hash of the push
--   push_time     ts     when the push landed
--   author        text   who pushed
--   commits       text?  packed list of commits in push order; records separated by 0x1e,
--                        fields by 0x1f: revision, first line of the commit message
--   perf_jobs     text?  packed list of perf job runs; records separated by 0x1e, fields by 0x1f:
--                        task label, result (success, testfailed, busted, exception, retry, ...),
--                        duration in seconds, tier, Taskcluster task id, retry id.
--                        Empty fields mean the value was NULL. Retriggers appear as separate records.
WITH perf_job_types AS (
  SELECT id, name
  FROM job_type
  WHERE name LIKE '%-browsertime-%'
     OR name LIKE '%-talos-%'
     OR name LIKE '%-awsy%'
     OR name LIKE '%-raptor-%'
     OR name LIKE 'perftest-%'
)
SELECT
  p.id AS push_id,
  repo.name AS repository,
  p.revision,
  p.time AS push_time,
  p.author,
  (
    SELECT string_agg(
      c.revision || E'\x1f' || COALESCE(split_part(c.comments, E'\n', 1), ''),
      E'\x1e' ORDER BY c.id
    )
    FROM commit c
    WHERE c.push_id = p.id
  ) AS commits,
  (
    SELECT string_agg(
      jt.name
        || E'\x1f' || COALESCE(j.result, '')
        || E'\x1f' || COALESCE(EXTRACT(EPOCH FROM (j.end_time - j.start_time))::int::text, '')
        || E'\x1f' || COALESCE(j.tier::text, '')
        || E'\x1f' || COALESCE(tm.task_id, '')
        || E'\x1f' || COALESCE(tm.retry_id::text, ''),
      E'\x1e'
    )
    FROM job j
    JOIN perf_job_types jt ON jt.id = j.job_type_id
    LEFT JOIN taskcluster_metadata tm ON tm.job_id = j.id
    WHERE j.push_id = p.id
  ) AS perf_jobs
FROM push p
JOIN repository repo ON repo.id = p.repository_id
WHERE repo.name IN ({repositories})
  AND p.time >= '{time_from}'
  AND p.time < '{time_to}'
ORDER BY p.id
