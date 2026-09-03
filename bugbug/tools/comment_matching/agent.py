from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain.chat_models import BaseChatModel, init_chat_model
from langchain.messages import HumanMessage
from pydantic import BaseModel, Field

from bugbug.tools.comment_matching.prompts import FIRST_MESSAGE_TEMPLATE, SYSTEM_PROMPT
from bugbug.tools.core.llms import DEFAULT_OPENAI_MODEL


class MatchingComment(BaseModel):
    id: int = Field(description="Unique identifier for the comment")
    content: str = Field(description="Content of the code review comment")
    file: str = Field(description="File path of the comment")


class CommentMatch(BaseModel):
    old_comment_id: int = Field(description="ID of the comment from the old set")
    new_comment_id: int = Field(description="ID of the comment from the new set")


class AgentResponse(BaseModel):
    matches: list[CommentMatch] = Field(
        description="List of matched comment pairs between old and new comments"
    )


class CommentMatchingTool:
    def __init__(self, llm: BaseChatModel):
        self.agent = create_agent(
            llm,
            system_prompt=SYSTEM_PROMPT,
            response_format=ProviderStrategy(AgentResponse),
        )

    @classmethod
    def create(cls, **kwargs):
        if "llm" not in kwargs:
            kwargs["llm"] = init_chat_model(DEFAULT_OPENAI_MODEL)

        return cls(**kwargs)

    def run(
        self, old_comments: list[MatchingComment], new_comments: list[MatchingComment]
    ) -> list[CommentMatch]:
        if not old_comments or not new_comments:
            return []

        response = self.agent.invoke(
            {
                "messages": [
                    HumanMessage(
                        FIRST_MESSAGE_TEMPLATE.format(
                            old_comments=old_comments,
                            new_comments=new_comments,
                        )
                    )
                ]
            }
        )

        return response["structured_response"].matches
