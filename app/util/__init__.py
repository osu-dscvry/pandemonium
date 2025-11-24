import secrets
import httpx
import time
import app.settings as settings
import jwt
import time

from typing import Optional
from fastapi import HTTPException

OSU_TOKEN_URL = "https://osu.ppy.sh/oauth/token"
OSU_API_ME_URL = "https://osu.ppy.sh/api/v2/me"
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_SECONDS = 60 * 60 * 24 * 30  # 30 days

def generate_state():
    return secrets.token_urlsafe(32)

async def exchange_code_for_token(code: str):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            OSU_TOKEN_URL,
            json={
                "client_id": settings.OSU_API_CLIENT_ID,
                "client_secret": settings.OSU_API_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.OSU_API_REDIRECT_URL,
            },
            timeout=10.0,
        )

        if response.status_code != 200:
            raise Exception(
                f"OAuth token exchange failed: {response.status_code} {response.text}"
            )

        data = response.json()
        data["expires_at"] = int(time.time()) + data["expires_in"]

        return data
    
async def get_osu_self(access_token: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            OSU_API_ME_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=10.0,
        )

        if response.status_code != 200:
            raise Exception(
                f"failed to fetch osu user: {response.status_code} {response.text}"
            )

        return response.json()

def generate_session_token(user_id: int, expires_in: Optional[int] = None) -> str:
    now = int(time.time())
    exp = now + (expires_in or JWT_EXPIRATION_SECONDS)

    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": exp,
    }

    return jwt.encode(payload, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)
