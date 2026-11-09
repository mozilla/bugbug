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

import logging
from pathlib import Path

log = logging.getLogger(__name__)


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
            self._ev = EvaluationLogger(
                name=name,
                model={"name": name, "metadata": model},
                dataset=data,
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

    def finish(self, pred, output: dict, scores: dict[str, float]) -> None:
        if pred is None:
            return
        for name, value in scores.items():
            pred.log_score(scorer=name, score=value)
        pred.finish(output)

    def summary(self, summary: dict) -> None:
        if self._ev:
            self._ev.log_summary(summary)
