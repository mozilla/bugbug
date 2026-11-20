# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""One Weave evaluation per run, logged example by example.

The model is the experiment's configuration, so Weave's comparison view pivots on
agent, harness, model, effort and dataset. The evaluation refers to the Weave
dataset itself and each prediction's inputs are its row, so evaluations over the
same rows line up example by example. A no-op when Weave tracing is not
configured.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path

log = logging.getLogger(__name__)


@functools.cache
def _agent_usage():
    """An op whose output Weave reads as one model's token usage."""
    import weave

    @weave.op(name="agent_usage")
    def agent_usage(model: str, usage: dict[str, int]) -> dict:
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_write = usage.get("cache_creation_input_tokens", 0)
        return {
            "model": model,
            "usage": {
                "input_tokens": usage.get("input_tokens", 0) + cache_read + cache_write,
                "output_tokens": usage.get("output_tokens", 0),
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
            },
        }

    return agent_usage


class WeaveLog:
    def __init__(self, name: str, model: dict, dataset: str, rows: list[dict]):
        self._ev = None
        try:
            import weave
            from weave import EvaluationLogger

            if weave.get_client() is None:
                return
            # A local directory has no Weave dataset to refer to.
            data = rows if Path(dataset).is_dir() else weave.ref(dataset).get()
            # Top-level fields, so Weave shows each as a column.
            self._ev = EvaluationLogger(
                name=name, model={**model, "name": name}, dataset=data
            )
        except Exception:
            log.exception("Weave evaluation logging is off for this run")

    @property
    def url(self) -> str | None:
        return self._ev.ui_url if self._ev else None

    def start(self, inputs: dict, example_id: str, rep: int):
        """Open an example's prediction; Weave times it until ``finish``."""
        if not self._ev:
            return None
        return self._ev.log_prediction(
            inputs=inputs, example_id=example_id, trial_index=rep
        )

    def finish(
        self,
        pred,
        output: dict,
        scores: dict[str, float],
        usage: dict[str, dict[str, int]] | None = None,
    ) -> None:
        """Close a prediction; ``usage`` is the agent's, by model, for Weave to cost.

        The agent's own model calls are traced in another process, so Weave would
        not count them in the evaluation's tokens and cost.
        """
        if pred is None:
            return
        from weave.trace.context import call_context

        with call_context.set_call_stack([pred.predict_call]):
            for model, counts in (usage or {}).items():
                _agent_usage()(model, counts)
        for name, value in scores.items():
            pred.log_score(scorer=name, score=value)
        pred.finish(output)

    def summary(self, summary: dict) -> None:
        if self._ev:
            self._ev.log_summary(summary)
