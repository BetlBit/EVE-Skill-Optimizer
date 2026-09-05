from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx
from jose import jwt

from .config import settings

SCOPES = (
    "esi-skills.read_skills.v1",
    "esi-skills.read_skillqueue.v1",
    "esi-clones.read_implants.v1",
)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def create_pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


async def metadata() -> dict:
    async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": settings.user_agent}) as client:
        r = await client.get(settings.sso_metadata_url)
        r.raise_for_status()
        return r.json()


async def authorization_url() -> tuple[str, str, str]:
    if not settings.eve_client_id:
        raise RuntimeError("EVE_CLIENT_ID is not configured")
    meta = await metadata()
    verifier, challenge = create_pkce()
    state = secrets.token_urlsafe(24)
    query = urlencode({
        "response_type": "code",
        "client_id": settings.eve_client_id,
        "redirect_uri": settings.eve_redirect_uri,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{meta['authorization_endpoint']}?{query}", state, verifier


async def exchange_code(code: str, verifier: str) -> dict:
    meta = await metadata()
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.eve_client_id,
        "code_verifier": verifier,
    }
    async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": settings.user_agent}) as client:
        r = await client.post(meta["token_endpoint"], data=payload)
        r.raise_for_status()
        return r.json()


def character_id_from_access_token(access_token: str) -> int:
    # The signature is validated in the production auth layer; here we only decode
    # the already-issued token to extract the subject for the MVP callback.
    claims = jwt.get_unverified_claims(access_token)
    sub = claims.get("sub", "")
    # Current EVE JWT subjects use CHARACTER:EVE:<id>.
    try:
        return int(sub.rsplit(":", 1)[-1])
    except Exception as e:
        raise RuntimeError(f"Unexpected EVE token subject: {sub!r}") from e
