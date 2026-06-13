"""Centralized JWT encode/decode.

All tokens are issued and verified through these helpers so that the issuer
(`iss`) and audience (`aud`) claims are applied and validated consistently.
Verifying `aud`/`iss` ensures a token minted for a different service/audience
cannot be replayed against this API even if it was signed with the same secret.
"""
from jose import jwt

from app.config import get_settings

settings = get_settings()


def encode_token(payload: dict) -> str:
    """Sign a JWT, stamping the configured issuer and audience."""
    to_encode = {
        **payload,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT (signature, expiry, issuer, audience).

    Raises jose.JWTError (or a subclass) on any validation failure.
    """
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
    )
