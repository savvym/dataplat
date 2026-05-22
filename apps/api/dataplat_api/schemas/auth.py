"""Pydantic schemas for the auth API surface — S007-F-007.

Only the response schema is defined here.  The request uses FastAPI's
OAuth2PasswordRequestForm (application/x-www-form-urlencoded with `username`
and `password` fields) which needs no explicit Pydantic model.
"""

from __future__ import annotations

from pydantic import BaseModel


class TokenResponse(BaseModel):
    """Response body for POST /api/auth/token (HTTP 200 OK).

    Fields match the F-007 verification requirement verbatim:
        {"access_token": "...", "token_type": "bearer"}

    access_token: signed JWT string (HS256, claims: sub, email, iat, exp).
    token_type:   always "bearer" — FastAPI OAuth2 convention.
    """

    access_token: str
    token_type: str
