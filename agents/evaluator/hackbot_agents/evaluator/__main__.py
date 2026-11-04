# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Evaluator: run an agent over a dataset, grade every example, log to Weave."""

from hackbot_runtime import AgentError, HackbotAgentResult, HackbotContext, run_async
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import dataset
from .runner import AGENTS, Experiment, Runner


class AgentInputs(BaseSettings):
    dataset: str
    agent: str = "prompt-executor"
    harness: str = "claude_code"
    model: str = "claude-sonnet-5-5"
    effort: str | None = None
    reps: int = 1
    tasks: list[str] = []
    example_timeout_min: int = 120
    judge_model: str = "claude-sonnet-5-5"
    # Weave prompt ref with the judge's rubric.
    judge_rubric: str
    # Appended to the experiment's name, for what its configuration doesn't show.
    label: str | None = None

    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)


class EvaluatorResult(HackbotAgentResult):
    experiment: str
    firefox_commit: str
    summary: dict
    weave_url: str | None = None


async def main(ctx: HackbotContext) -> EvaluatorResult:
    inputs = AgentInputs()
    if inputs.agent not in AGENTS:
        raise AgentError(f"unknown agent {inputs.agent!r}; known: {', '.join(AGENTS)}")
    rows = dataset.load(inputs.dataset)
    if inputs.tasks:
        rows = [row for row in rows if row["id"] in inputs.tasks]
    if not rows:
        raise AgentError(f"no examples to run in {inputs.dataset}")
    exp = Experiment(**inputs.model_dump(exclude={"tasks"}))
    return EvaluatorResult(num_turns=0, **await Runner(exp, ctx, rows).run())


if __name__ == "__main__":
    run_async(main)
