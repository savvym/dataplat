"""Auth router — S007-F-007.

Provides POST /api/auth/token: validates credentials and issues a JWT.
This route is intentionally PUBLIC — it is the token issuer; all other routes
consume the JWT via get_current_user (dataplat_api.auth.dependencies).

Design decisions (see contracts/S007-F-007/agreed.md §4):
  - Password hashing: direct bcrypt (passlib dropped — incompatible with bcrypt>=4.0).
  - JWT library: PyJWT (python-jose has known CVEs and is unmaintained).
  - Algorithm: HS256 (symmetric, single-tenant MVP).
  - Claim set: sub (user id), email, iat, exp.
  - Request: OAuth2PasswordRequestForm (form-encoded username+password) for
    Swagger "Try it out" compatibility and F-008 OAuth2PasswordBearer plug-in.
  - Timing safety: _DUMMY_HASH ensures bcrypt.checkpw() runs even when the
    user is not found, preventing timing-based email enumeration attacks.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplat_api.config import settings
from dataplat_api.db.models import User
from dataplat_api.db.session import get_session
from dataplat_api.schemas.auth import TokenResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Computed once at module load — used for constant-time verify when user not found.
# rounds=12 matches the real hash call so both call sites are visually consistent.
# (reviewer impl note 1)
_DUMMY_HASH: bytes = bcrypt.hashpw(b"dummy", bcrypt.gensalt(rounds=12))


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Obtain a JWT access token",
    description=(
        "Validates email (submitted as `username`) and password via bcrypt. "
        "Returns a signed HS256 JWT on success. "
        "Returns HTTP 401 for incorrect credentials (same message whether the "
        "user is not found or the password is wrong — prevents email enumeration). "
        "Public route — all other API routes require Bearer <token> (F-008)."
    ),
)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    """Issue a JWT for valid credentials.

    The `username` field of the OAuth2 form is treated as the user's email address.

    Timing safety: bcrypt.checkpw() is called even when the user is not found
    (against _DUMMY_HASH) so that response time does not reveal whether the email
    is registered.
    """
    # Look up user by email (username field = email in this system).
    result = await session.execute(select(User).where(User.email == form_data.username))
    user: User | None = result.scalars().first()

    if user is None:
        # Constant-time dummy check — prevents timing-based email enumeration.
        bcrypt.checkpw(form_data.password.encode("utf-8"), _DUMMY_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not bcrypt.checkpw(
        form_data.password.encode("utf-8"),
        user.hashed_password.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Build JWT claims.
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "iat": now,
        "exp": now + timedelta(seconds=settings.JWT_TTL_SECONDS),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    return TokenResponse(access_token=token, token_type="bearer")
