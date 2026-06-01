import hmac

from fastapi import Header, HTTPException, status

from app.config import settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not settings.external_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API key not configured",
        )
    if x_api_key is None or not hmac.compare_digest(
        x_api_key, settings.external_api_key
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key",
        )
