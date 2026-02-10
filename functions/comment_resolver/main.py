import logging

import flask
import functions_framework
from langchain.chat_models import init_chat_model

from bugbug.tools.comment_resolver import CodeGeneratorTool
from bugbug.tools.core.llms import DEFAULT_OPENAI_MODEL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@functions_framework.http
def handle_code_generation(request: flask.Request):
    revision_id = None

    if request.method == "GET":
        revision_id = request.args.get("revisionID")
    elif request.method == "POST":
        data = request.get_json()
        if not data:
            return "Invalid JSON payload", 400
        revision_id = data.get("revisionID")
    else:
        return "Only GET and POST requests are allowed", 405

    if not revision_id:
        return "Missing revisionID", 400

    try:
        llm = init_chat_model(DEFAULT_OPENAI_MODEL)
        codegen = CodeGeneratorTool(
            client=None,
            model="gpt-4",
            hunk_size=10,
            llm=llm,
        )
        result = codegen.generate_fixes_for_all_comments(int(revision_id))
        return result, 200

    except Exception as e:
        logger.exception("Error processing request")
        return {"error": str(e)}, 500
