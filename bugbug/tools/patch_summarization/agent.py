from langchain.agents import create_agent
from langchain.chat_models import BaseChatModel, init_chat_model
from langchain.messages import HumanMessage

from bugbug.tools.base import GenerativeModelTool
from bugbug.tools.code_review.utils import format_patch_set
from bugbug.tools.core.llms import DEFAULT_ANTHROPIC_MODEL
from bugbug.tools.core.platforms.base import Patch
from bugbug.tools.patch_summarization.prompts import PROMPT_TEMPLATE_SUMMARIZATION


class PatchSummarizationTool(GenerativeModelTool):
    def __init__(self, llm: BaseChatModel, target_software: str = "Mozilla Firefox"):
        self.target_software = target_software
        self.agent = create_agent(llm)

    @classmethod
    def create(cls, **kwargs):
        """Factory method to instantiate the tool with default dependencies.

        This method takes the same parameters as the constructor, but all
        parameters are optional. If a parameter is not provided, a default
        component will be created and used.
        """
        if "llm" not in kwargs:
            kwargs["llm"] = init_chat_model(DEFAULT_ANTHROPIC_MODEL)

        return cls(**kwargs)

    def run(self, patch: Patch):
        result = self.agent.invoke(
            {
                "messages": [
                    HumanMessage(
                        PROMPT_TEMPLATE_SUMMARIZATION.format(
                            target_software=self.target_software,
                            patch=format_patch_set(patch.patch_set),
                            bug_title=patch.bug_title,
                            patch_title=patch.patch_title,
                            patch_description=patch.patch_description,
                        )
                    )
                ]
            }
        )

        return result["messages"][-1].content
