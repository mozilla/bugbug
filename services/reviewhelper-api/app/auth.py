import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

bearer_scheme = HTTPBearer()


async def verify_external_api_key(
    auth: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> None:
    """Verify the Bearer key for public API endpoints."""
    if not secrets.compare_digest(auth.credentials, settings.external_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


async def verify_internal_api_key(
    auth: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> None:
    """Verify the Bearer key for requests coming from Cloud Tasks."""
    if not secrets.compare_digest(auth.credentials, settings.internal_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
