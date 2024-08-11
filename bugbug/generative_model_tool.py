# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from abc import ABC, abstractmethod
from logging import INFO, basicConfig, getLogger
from typing import Any

from langchain_community.llms import HumanInputLLM
from langchain_openai import AzureChatOpenAI, ChatOpenAI

from bugbug.utils import get_secret

basicConfig(level=INFO)
logger = getLogger(__name__)


def create_llm(llm):
    openai_temperature = 0.2
    if llm == "human":
        return HumanInputLLM()
    elif llm == "openai":
        return ChatOpenAI(
            model_name="gpt-4o-2024-05-13",
            api_key=get_secret("OPENAI_API_KEY"),
            temperature=openai_temperature,
        )
    elif llm == "azureopenai":
        return AzureChatOpenAI(
            azure_endpoint=get_secret("OPENAI_API_ENDPOINT"),
            azure_deployment=get_secret("OPENAI_API_DEPLOY"),
            api_key=get_secret("OPENAI_API_KEY"),
            api_version=get_secret("OPENAI_API_VERSION"),
            temperature=openai_temperature,
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
        self._set_tokenizer(llm.model_name)

    def _set_tokenizer(self, model_name: str) -> None:
        import tiktoken

        try:
            self._tokenizer = tiktoken.encoding_for_model(model_name)
        except KeyError:
            FALLBACK_ENCODING = "cl100k_base"
            logger.info(
                "Tokenizer couldn't be found for %s, falling back to %s",
                model_name,
                FALLBACK_ENCODING,
            )
            self._tokenizer = tiktoken.get_encoding(FALLBACK_ENCODING)

    def count_tokens(self, text):
        return len(self._tokenizer.encode(text))

    @abstractmethod
    def run(self, *args, **kwargs) -> Any:
        ...
