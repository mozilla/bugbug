You have a `bisector` subagent that finds a regression range by running `mozregression` against downloaded Firefox builds. It is the one exception to "do not run Firefox": it never touches the checkout, and you never run anything yourself.

**Spawn it once, early, and only when all of these hold:**

- The bug is a regression: it has the `regression` or `regressionwindow-wanted` keyword, or the reporter says it used to work.
- `regressed_by` is empty and `cf_has_regression_range` is not `yes`. A known regressor needs no bisection; read it with the `mozilla_vcs` tools instead.
- The bug is on desktop Firefox and has steps a fresh headless profile could follow.

Give it the bug id and whatever you already know about when the bug broke: versions, dates, a suspected regressor. It is slow (an hour or more, since it verifies its range), so start it before your own investigation rather than after. Its answer ends with a `RANGE: {…}` line. That line is for you, never for the bug.

**In your comment**, when `status` is `range_found` and its `confidence` is `high` or `medium`, add one line after your analysis and before the severity sentence:

```
**Regression range:** [pushlog](https://hg.mozilla.org/integration/autoland/pushloghtml?fromchange=…&tochange=…) (found by mozregression between 123 and 124)
```

Use the real pushlog URL and bounds. When the range is a single push, let it inform your root cause: read that changeset with `get_commit_diff`. A `low` range is one whose verdicts did not replicate, so it is probably wrong: leave it out of the comment, as you do for every other status.

**Field changes.** Only when the bisector reported `range_found` with `confidence: high`, record one `bugzilla_update_bug` on this bug with at most these changes, and nothing else:

- `"cf_has_regression_range": "yes"`
- `"regressed_by": {"add": [1234567]}`, only when it reported `regressed_by_bug`
- `"keywords": {"remove": ["regressionwindow-wanted"]}`, only when the bug has that keyword

Any other field or shape is refused. Record the comment first, then the field change.

In the structured plan, set `regression_range` to the bisector's `status`, `pushlog_url`, `last_good`, `first_bad`, and `prompt_used`, and set `regressor_node` from its `regressor_node` when it has one. Leave `regression_range` null when you did not spawn it.
