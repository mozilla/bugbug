# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Test case and test step generation tool implementation."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.chat_models import BaseChatModel, init_chat_model
from langchain.messages import HumanMessage

from bugbug.tools.base import GenerativeModelTool
from bugbug.tools.core.llms import DEFAULT_OPENAI_MODEL
from bugbug.tools.test_plans_generator.data_types import TestPlanGenerationResult
from bugbug.tools.test_plans_generator.prompts import (
    TEST_CASES_PROMPT_TEMPLATE,
    TEST_STEPS_PROMPT_TEMPLATE,
)


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        return "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )

    return str(content)


class TestPlanGenerationTool(GenerativeModelTool):
    """Tool for generating QA test cases and test steps."""

    def __init__(
        self,
        llm: BaseChatModel,
        target_software: str = "Mozilla Firefox",
    ) -> None:
        self.target_software = target_software
        self.agent = create_agent(llm)

    @classmethod
    def create(cls, **kwargs):
        """Factory method to instantiate the tool with default dependencies."""
        if "llm" not in kwargs:
            kwargs["llm"] = init_chat_model(DEFAULT_OPENAI_MODEL)

        return cls(**kwargs)

    def _invoke_llm(self, prompt: str) -> str:
        result = self.agent.invoke({"messages": [HumanMessage(prompt)]})
        return _message_content_to_text(result["messages"][-1].content).strip()

    def generate_test_cases(
        self,
        feature_description: str,
        test_scope: str,
        qa_test_cases: str = "",
    ) -> str:
        """Generate missed test cases for a feature."""
        prompt = TEST_CASES_PROMPT_TEMPLATE.format(
            target_software=self.target_software,
            feature_description=feature_description,
            test_scope=test_scope,
            qa_test_cases=qa_test_cases or "N/A",
        )
        return self._invoke_llm(prompt)

    def generate_test_steps(
        self,
        feature_description: str,
        test_cases: str,
    ) -> str:
        """Generate detailed test steps for each test case."""
        prompt = TEST_STEPS_PROMPT_TEMPLATE.format(
            target_software=self.target_software,
            feature_description=feature_description,
            test_cases=test_cases,
        )
        return self._invoke_llm(prompt)

    def run(
        self,
        feature_description: str,
        test_scope: str,
        qa_test_cases: str = "",
        generate_steps: bool = True,
    ) -> TestPlanGenerationResult:
        """Generate test cases and optionally generate steps for them."""
        generated_test_cases = self.generate_test_cases(
            feature_description,
            test_scope,
            qa_test_cases,
        )

        test_steps = None
        if generate_steps and generated_test_cases:
            test_steps = self.generate_test_steps(
                feature_description,
                generated_test_cases,
            )

        return TestPlanGenerationResult(
            test_cases=generated_test_cases,
            test_steps=test_steps,
        )
