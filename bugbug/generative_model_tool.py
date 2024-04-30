# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from abc import ABC, abstractmethod
from typing import Any

from langchain.llms.human import HumanInputLLM
from langchain_openai import ChatOpenAI

from bugbug.utils import get_secret


def create_llm(llm):
    if llm == "human":
        return HumanInputLLM()
    elif llm == "openai":
        return ChatOpenAI(
            model_name="gpt-4-0125-preview",
            api_key=get_secret("OPENAI_API_KEY"),
            temperature=0.2,
        )
    else:
        raise NotImplementedError


class GenerativeModelTool(ABC):
    @property
    @abstractmethod
    def version(self) -> str:
        ...

    def __init__(self, llm, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.llm = llm

    @abstractmethod
    def run(self, *args, **kwargs) -> Any:
        ...
