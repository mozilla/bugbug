import logging
import os
from types import SimpleNamespace

import flask
import functions_framework

from bugbug import generative_model_tool
from bugbug.tools.release_notes import ReleaseNotesCommitsSelector
from bugbug.utils import get_secret

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@functions_framework.http
def handle_release_notes(request: flask.Request) -> flask.Response:
    if request.method != "GET":
        return flask.Response("Only GET requests are allowed", status=405)

    version = request.args.get("version")
    if not version:
        return flask.Response("Missing 'version' query parameter", status=400)

    try:
        os.environ["OPENAI_API_KEY"] = get_secret("OPENAI_API_KEY")
    except Exception as e:
        return flask.Response(f"Failed to load OpenAI key: {str(e)}", status=500)

    args = build_args_from_request(request)

    try:
        llm = generative_model_tool.create_llm_from_args(args)
        selector = ReleaseNotesCommitsSelector(chunk_size=args.chunk_size, llm=llm)
        notes = selector.get_final_release_notes_commits(version=args.version)

        if not notes:
            return flask.Response("No user-facing commits found.", status=404)

        return flask.Response(notes, mimetype="text/plain")
    except Exception as e:
        logger.exception("Failed to generate release notes")
        return flask.Response(f"Internal Server Error: {str(e)}", status=500)


def build_args_from_request(request: flask.Request):
    def get(key, default=None, type_fn=str):
        value = request.args.get(key)
        return type_fn(value) if value is not None else default

    llm = get("llm", default="openai")

    args = {
        "llm": llm,
        "version": get("version"),
        "chunk_size": get("chunk_size", default=100, type_fn=int),
    }

    for arg_name in request.args:
        if arg_name.startswith(f"{llm}_"):
            args[arg_name] = request.args.get(arg_name)

    return SimpleNamespace(**args)
