import logging
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI

from app import __version__
from app.config import settings
from app.database.connection import close_db, init_db
from app.routers import events_router, runs_router, webhooks_router

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=f"hackbot-api@{__version__}",
        send_default_pii=True,
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        yield
    finally:
        await close_db()


app = FastAPI(
    title="Hackbot API",
    description="Agent orchestration platform that runs agents as Cloud Run Jobs",
    version=__version__,
    lifespan=lifespan,
)

app.include_router(runs_router)
app.include_router(events_router)
app.include_router(webhooks_router)


@app.get("/health")
async def health_check():
    """Health check endpoint for Cloud Run."""
    return {"message": "Service is healthy", "status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
