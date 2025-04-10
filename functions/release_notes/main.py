import logging
import os

import flask
import functions_framework

from bugbug import generative_model_tool
from bugbug.tools.release_notes import ReleaseNotesCommitsSelector
from bugbug.utils import get_secret

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@functions_framework.http
def handle_release_notes(request: flask.Request):
    if request.method != "GET":
        return "Only GET requests are allowed", 405

    version = request.args.get("version")
    if not version:
        return "Missing 'version' query parameter", 400

    os.environ["OPENAI_API_KEY"] = get_secret("OPENAI_API_KEY")

    llm_name = request.args.get("llm", "openai")
    chunk_size = int(request.args.get("chunk_size", 100))
    version = request.args.get("version")

    llm = generative_model_tool.create_llm_from_request(llm_name, request.args)
    selector = ReleaseNotesCommitsSelector(chunk_size=chunk_size, llm=llm)
    notes = selector.get_final_release_notes_commits(version=version)

    if not notes:
        return "No user-facing commits found.", 404

    return notes, 200, {"Content-Type": "text/plain"}
