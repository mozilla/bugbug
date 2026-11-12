-- The project's performance tasks, as a predicate on fxci.tasks. The queries
-- reading perf tasks fill it in as {perf_task_filter}; it is parenthesized so
-- it can be negated. Unqualified columns of fxci.tasks resolve in the joins
-- with fxci.task_runs, which has no tags.
(
  tags.project = '{repository}'
  AND (
    tags.label LIKE "%-browsertime-%"
    OR tags.label LIKE "%-talos-%"
    OR tags.label LIKE "%-awsy%"
    OR tags.label LIKE "%-raptor-%"
    OR tags.label LIKE "perftest-%"
  )
)
