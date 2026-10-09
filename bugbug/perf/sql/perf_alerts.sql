-- One row per performance alert, joined with the summary it is currently
-- attached to (reassigned alerts follow related_summary_id, like the perf
-- team's "All Alert Summaries" query), its signature, the pushes bounding the
-- alert range, and the task labels that produced the alerting datapoints.
--
-- Runs against the Treeherder Postgres replica exposed by STMO.
--
-- Columns (one row per alert):
--   Alert
--   alert_id                  int      Perfherder alert id
--   alert_status              int      0 untriaged, 1 downstream, 2 reassigned, 3 invalid, 4 acknowledged, 5 confirming
--   is_regression             bool     direction of the shift for this series (false is an improvement)
--   amount_pct, amount_abs    float    size of the shift, relative and absolute
--   t_value                   float    Student t statistic Perfherder used to raise the alert
--   prev_value, new_value     float    series value before and after the shift
--   noise_profile             text     OK, OUTLIERS, SKEWED, MODAL or N/A; how noisy the series is
--   alert_manually_created    bool     alert created by a sheriff rather than the detector
--   alert_sheriffed           bool     series is part of the sheriffed set
--   alert_summary_id          int      summary the alert was created under
--   related_summary_id        int?     summary it was reassigned to, when a sheriff moved it
--   alert_created             ts       when the detector raised it
--   Summary (the one the alert currently belongs to)
--   summary_id                int      COALESCE(related_summary_id, alert_summary_id)
--   summary_status            int      0 untriaged, 1 downstream, 2 reassigned, 3 invalid, 4 improvement,
--                                      5 investigating, 6 wontfix, 7 fixed, 8 backedout, 9 infra
--   summary_created           ts       when the summary was opened
--   first_triaged             ts?      first sheriff action
--   last_updated              ts       last change to the summary
--   bug_number                int?     bug filed or linked by the sheriff
--   bug_status                int?     Perfherder's cached Bugzilla status code for that bug
--   bug_updated               ts?      when the bug was linked
--   summary_manually_created  bool     summary created by hand
--   summary_sheriffed         bool     false for automated monitor summaries
--   notes                     text?    free-text sheriff notes
--   Attribution range
--   push_id, prev_push_id     int      culprit push and the last push before it with a datapoint
--   original_push_id,         int      the same pair as first detected, before any reassignment
--   original_prev_push_id
--   revision, push_time       text,ts  culprit push as changeset hash and timestamp
--   prev_push_revision,       text,ts  previous push as changeset hash and timestamp (null for a first datapoint)
--   prev_push_time
--   Series
--   repository                text     autoland or mozilla-central
--   framework                 text     browsertime, talos, awsy, mozperftest, build_metrics, ...
--   signature_id              int      Perfherder series id
--   signature_hash            text     stable hash of the series identity
--   suite, test               text     e.g. amazon and fcp; test is the subtest, empty for a suite summary
--   extra_options             text     space-separated options such as cold fission webrender
--   application               text     firefox, chrome, fenix, ... (empty for some frameworks)
--   lower_is_better           bool     direction in which the metric improves
--   should_alert              bool?    series configured to alert at all
--   alert_severity            text     critical, subcritical or normal, as set in-tree
--   measurement_unit          text?    ms, score, bytes, ...
--   signature_tags            text     space-separated tags on the series
--   platform                  text     machine platform, e.g. linux2404-64-shippable
--   Aggregates (pipe-joined, split by the Python side)
--   summary_tags              text?    sheriff tags on the summary, e.g. harness or infra
--   task_labels               text?    task types whose datapoints triggered the alert
--   task_ids                  text?    Taskcluster task ids of those datapoints
SELECT
  a.id AS alert_id,
  a.status AS alert_status,
  a.is_regression,
  a.amount_pct,
  a.amount_abs,
  a.t_value,
  a.prev_value,
  a.new_value,
  a.noise_profile,
  a.manually_created AS alert_manually_created,
  a.sheriffed AS alert_sheriffed,
  a.summary_id AS alert_summary_id,
  a.related_summary_id,
  a.created AS alert_created,
  s.id AS summary_id,
  s.status AS summary_status,
  s.created AS summary_created,
  s.first_triaged,
  s.last_updated,
  s.bug_number,
  s.bug_status,
  s.bug_updated,
  s.manually_created AS summary_manually_created,
  s.sheriffed AS summary_sheriffed,
  s.notes,
  s.push_id,
  s.prev_push_id,
  s.original_push_id,
  s.original_prev_push_id,
  push.revision,
  push.time AS push_time,
  prev_push.revision AS prev_push_revision,
  prev_push.time AS prev_push_time,
  repo.name AS repository,
  fw.name AS framework,
  sig.id AS signature_id,
  sig.signature_hash,
  sig.suite,
  sig.test,
  sig.extra_options,
  sig.application,
  sig.lower_is_better,
  sig.should_alert,
  sig.alert_severity,
  sig.measurement_unit,
  sig.tags AS signature_tags,
  mp.platform,
  (
    SELECT string_agg(DISTINCT t.name, '|')
    FROM performance_tag_alert_summaries tas
    JOIN performance_tag t ON t.id = tas.performancetag_id
    WHERE tas.performancealertsummary_id = s.id
  ) AS summary_tags,
  (
    SELECT string_agg(DISTINCT jt.name, '|')
    FROM performance_datum d
    JOIN job j ON j.id = d.job_id
    JOIN job_type jt ON jt.id = j.job_type_id
    WHERE d.repository_id = s.repository_id
      AND d.signature_id = a.series_signature_id
      AND d.push_id IN (s.push_id, os.push_id)
  ) AS task_labels,
  (
    SELECT string_agg(DISTINCT tm.task_id, '|')
    FROM performance_datum d
    JOIN job j ON j.id = d.job_id
    JOIN taskcluster_metadata tm ON tm.job_id = j.id
    WHERE d.repository_id = s.repository_id
      AND d.signature_id = a.series_signature_id
      AND d.push_id IN (s.push_id, os.push_id)
  ) AS task_ids
FROM performance_alert a
JOIN performance_alert_summary os ON os.id = a.summary_id
JOIN performance_alert_summary s ON s.id = COALESCE(a.related_summary_id, a.summary_id)
JOIN repository repo ON repo.id = s.repository_id
JOIN performance_framework fw ON fw.id = s.framework_id
JOIN push ON push.id = s.push_id
LEFT JOIN push prev_push ON prev_push.id = s.prev_push_id
JOIN performance_signature sig ON sig.id = a.series_signature_id
JOIN machine_platform mp ON mp.id = sig.platform_id
WHERE repo.name IN ({repositories})
  AND s.created >= '{created_from}'
  AND s.created < '{created_to}'
ORDER BY s.id, a.id
