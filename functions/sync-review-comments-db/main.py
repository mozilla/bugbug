import logging

import functions_framework
from database import init_connection_poole_engine
from models import (
    Evaluation,
    Suggestion,
)
from qdrant_client import QdrantClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from bugbug.tools import code_review
from bugbug.utils import get_secret
from bugbug.vectordb import QdrantVectorDB

logging.basicConfig()
logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)

pg_engine = init_connection_poole_engine()
qdrant_client = QdrantClient(
    location=get_secret("QDRANT_LOCATION"), api_key=get_secret("QDRANT_API_KEY")
)


def get_recent_evaluations(min_id: int):
    with Session(pg_engine) as session:
        stmt = (
            select(Evaluation, Suggestion)
            .join(Suggestion)
            .where(Evaluation.id > min_id)
        )

        evaluations = session.scalars(stmt)
        yield from evaluations


@functions_framework.cloud_event
def event_handler(cloud_event):
    vector_db = QdrantVectorDB("suggestions_feedback")
    vector_db.setup()

    largest_evaluation_id = vector_db.get_largest_id()
    logging.info(
        "Retrieving evaluations from the PostgreSQL database starting from evaluation ID %d",
        largest_evaluation_id,
    )

    feedback_db = code_review.SuggestionsFeedbackDB(vector_db)
    feedback_db.add_suggestions_feedback(
        code_review.SuggestionFeedback(
            id=evaluation.id,
            action=evaluation.action.name,
            comment=evaluation.suggestion.content,
            file_path=evaluation.suggestion.file_path,
            user=evaluation.user,
        )
        for evaluation in get_recent_evaluations(largest_evaluation_id)
    )
