import flask
import functions_framework

from bugbug import generative_model_tool
from bugbug.tools.release_notes import ReleaseNotesCommitsSelector

tool: ReleaseNotesCommitsSelector | None = None

DEFAULT_CHUNK_SIZE = 1000


@functions_framework.http
def handle_release_notes(request: flask.Request):
    global tool

    if request.method != "GET":
        return "Only GET requests are allowed", 405

    release = int(request.args.get("release"))
    channel = request.args.get("channel")

    if not release or not channel:
        return "Missing 'release' or 'channel' query parameter", 400

    if tool is None:
        llm = generative_model_tool.create_openai_llm()
        tool = ReleaseNotesCommitsSelector(chunk_size=DEFAULT_CHUNK_SIZE, llm=llm)

    commit_list = tool.get_final_release_notes_commits(
        target_release=release, channel=channel
    )

    return {"commits": commit_list}, 200
