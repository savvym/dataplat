"""JWT Bearer enforcement dependency — S008-F-008.

Provides:
  - oauth2_scheme: OAuth2PasswordBearer instance (tokenUrl="/api/auth/token",
    auto_error=True — FastAPI raises HTTP 401 with WWW-Authenticate: Bearer when
    the Authorization header is absent; never set auto_error=False).
  - get_current_user: FastAPI dependency that decodes a Bearer JWT and returns
    the User ORM object. All four failure modes (missing token, malformed/invalid
    signature, expired, user-not-found) return HTTP 401 with the SAME detail
    message to prevent information leakage (anti-enumeration).

Import path for other routers:
    from dataplat_api.auth.dependencies import get_current_user
"""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplat_api.config import settings
from dataplat_api.db.models import User
from dataplat_api.db.session import get_session

# auto_error=True (default) — FastAPI raises HTTP 401 with WWW-Authenticate: Bearer
# automatically when the Authorization: Bearer header is absent.  DO NOT set False.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

# Canonical 401 detail — same across all failure modes to prevent enumeration.
_CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: AsyncSession = Depends(get_session),
) -> User:
    """Decode a Bearer JWT and return the corresponding User row.

    Failure modes (all raise HTTP 401 with WWW-Authenticate: Bearer):
      1. Missing Authorization header — handled automatically by oauth2_scheme
         (auto_error=True); this function is never called in that case.
      2. Malformed token / invalid signature / any jwt.InvalidTokenError.
      3. Expired token (jwt.ExpiredSignatureError, a subclass of InvalidTokenError).
      4. Subject user no longer exists in the database.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        raise _CREDENTIALS_EXCEPTION
    except jwt.InvalidTokenError:
        raise _CREDENTIALS_EXCEPTION

    sub: str | None = payload.get("sub")
    if sub is None:
        raise _CREDENTIALS_EXCEPTION

    try:
        user_id = int(sub)
    except (ValueError, TypeError):
        raise _CREDENTIALS_EXCEPTION

    result = await session.execute(select(User).where(User.id == user_id))
    user: User | None = result.scalar_one_or_none()
    if user is None:
        raise _CREDENTIALS_EXCEPTION

    return user
