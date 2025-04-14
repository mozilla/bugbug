import logging
import os

import flask
import functions_framework

from bugbug import generative_model_tool
from bugbug.tools.release_notes import ReleaseNotesCommitsSelector
from bugbug.utils import get_secret

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

os.environ["OPENAI_API_KEY"] = get_secret("OPENAI_API_KEY")

tool: ReleaseNotesCommitsSelector | None = None

DEFAULT_LLM_NAME = "openai"
DEFAULT_CHUNK_SIZE = 1000


@functions_framework.http
def handle_release_notes(request: flask.Request):
    global tool

    if request.method != "GET":
        return "Only GET requests are allowed", 405

    version = request.args.get("version")
    if not version:
        return "Missing 'version' query parameter", 400

    if (
        tool is None
        or tool.llm_name != DEFAULT_LLM_NAME
        or tool.chunk_size != DEFAULT_CHUNK_SIZE
    ):
        logger.info("Initializing new ReleaseNotesCommitsSelector...")
        llm = generative_model_tool.create_llm_from_request(DEFAULT_LLM_NAME, {})
        tool = ReleaseNotesCommitsSelector(chunk_size=DEFAULT_CHUNK_SIZE, llm=llm)
        tool.llm_name = DEFAULT_LLM_NAME
        tool.chunk_size = DEFAULT_CHUNK_SIZE

    notes = tool.get_final_release_notes_commits(version=version)

    if not notes:
        return "", 200, {"Content-Type": "text/plain"}

    return notes, 200, {"Content-Type": "text/plain"}
