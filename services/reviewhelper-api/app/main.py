import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.database.connection import close_db, init_db
from app.routers import feedback_router, internal_router, request_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()


app = FastAPI(
    title="Review Helper Backend",
    description="FastAPI backend for Mozilla's Review Helper supporting Phabricator and GitHub",
    version="0.1.0",
    lifespan=lifespan,
)


app.include_router(request_router)
app.include_router(feedback_router)
app.include_router(internal_router)


@app.get("/health")
async def health_check():
    """Health check endpoint for Cloud Run."""
    return {"message": "Service is healthy", "status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
