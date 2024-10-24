# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import inspect
import re
from abc import ABC, abstractmethod
from logging import INFO, basicConfig, getLogger
from typing import Any

from bugbug.utils import get_secret

basicConfig(level=INFO)
logger = getLogger(__name__)


def create_human_llm():
    from langchain_community.llms import HumanInputLLM

    return HumanInputLLM()


def create_openai_llm(temperature=0.2, top_p=None):
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model_name="gpt-4o-2024-05-13",
        api_key=get_secret("OPENAI_API_KEY"),
        temperature=temperature,
        top_p=top_p,
    )


def create_azureopenai_llm(temperature=0.2, top_p=None):
    from langchain_openai import AzureChatOpenAI

    return AzureChatOpenAI(
        azure_endpoint=get_secret("OPENAI_API_ENDPOINT"),
        azure_deployment=get_secret("OPENAI_API_DEPLOY"),
        api_key=get_secret("OPENAI_API_KEY"),
        api_version=get_secret("OPENAI_API_VERSION"),
        temperature=temperature,
        top_p=top_p,
    )


def create_anthropic_llm(temperature=0.2, top_p=None):
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model_name="claude-3-5-sonnet-20241022",
        api_key=get_secret("ANTHROPIC_API_KEY"),
        temperature=temperature,
        top_p=top_p,
    )


def create_gemini_llm(temperature=0.2, top_p=None):
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model="gemini-1.5-pro",
        api_key=get_secret("GOOGLE_API_KEY"),
        temperature=temperature,
        top_p=top_p,
    )


def create_mistral_llm(temperature=0.2, top_p=None):
    from langchain_mistralai import ChatMistralAI

    return ChatMistralAI(
        model_name="mistral-large-latest",
        api_key=get_secret("MISTRAL_API_KEY"),
        temperature=temperature,
        top_p=top_p,
    )


def create_local_llm(
    model_path,
    n_gpu_layers=0,
    n_batch=512,
    n_ctx=4096,
    max_tokens=0,
    temperature=0.2,
    top_p=1.0,
):
    from langchain_community.chat_models import ChatLlamaCpp

    return ChatLlamaCpp(
        model_path=model_path,
        n_gpu_layers=n_gpu_layers,
        n_batch=n_batch,
        n_ctx=n_ctx,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )


def _get_llms_with_create_funcs():
    llm_to_create_map = {}
    for name in list(globals()):
        match = re.search(r"create_(.*?)_llm", name)
        if match is None:
            continue

        llm_name = match.group(1)
        create_llm_function = globals()[f"create_{llm_name}_llm"]
        llm_to_create_map[llm_name] = inspect.signature(create_llm_function).parameters

    return llm_to_create_map


AVAILABLE_LLMS = _get_llms_with_create_funcs()


def create_llm_to_args(parser):
    parser.add_argument(
        "--llm",
        help="LLM",
        required=True,
        choices=AVAILABLE_LLMS.keys(),
    )
    for llm_name, llm_arguments in AVAILABLE_LLMS.items():
        group = parser.add_argument_group(f"Options for '{llm_name}' LLM")
        for llm_argument in llm_arguments.values():
            group.add_argument(
                f"--{llm_name}-{llm_argument.name}",
                default=llm_argument.default
                if llm_argument.default is not llm_argument.empty
                else None,
                help=llm_argument.name,
            )


def create_llm_from_args(args):
    if args.llm not in AVAILABLE_LLMS:
        raise NotImplementedError

    llm_creation_args = {}
    for arg_name, arg_value in vars(args).items():
        if arg_name.startswith(f"{args.llm}_"):
            llm_creation_args[arg_name[len(f"{args.llm}_") :]] = arg_value
    return globals()[f"create_{args.llm}_llm"](**llm_creation_args)


def get_tokenizer(model_name):
    import tiktoken

    try:
        return tiktoken.encoding_for_model(model_name)
    except KeyError:
        FALLBACK_ENCODING = "cl100k_base"
        logger.info(
            "Tokenizer couldn't be found for %s, falling back to %s",
            model_name,
            FALLBACK_ENCODING,
        )
        return tiktoken.get_encoding(FALLBACK_ENCODING)


class GenerativeModelTool(ABC):
    @property
    @abstractmethod
    def version(self) -> str:
        ...

    @abstractmethod
    def run(self, *args, **kwargs) -> Any:
        ...

    @staticmethod
    def _print_answer(answer):
        print(f"\u001b[33;1m\033[1;3m{answer}\u001b[0m")
