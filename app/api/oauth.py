import httpx
import urllib.parse as urllib
from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import RedirectResponse
from .state import APIState, get_state
import app.settings as settings
from app.util import generate_state, exchange_code_for_token, get_osu_self, generate_session_token

router = APIRouter()

@router.get("/oauth/login")
async def login(
    state: APIState = Depends(get_state)
):
    state_token = generate_state()
    params = {
        "response_type": "code",
        "client_id": settings.OSU_API_CLIENT_ID,
        "redirect_uri": settings.OSU_API_REDIRECT_URL,
        "scope": "public identify",
        "state": state_token
    }

    await state.redis.setex(f"pandemonium:oauth_state:{state_token}", 300, "1")
    return RedirectResponse(f"https://osu.ppy.sh/oauth/authorize?{urllib.urlencode(params)}")

@router.get("/oauth/callback")
async def callback(
    code: str = Query(),
    state: str = Query(),
    api_state: APIState = Depends(get_state)
):
    key = f"pandemonium:oauth_state:{state}"
    exists = await api_state.redis.get(key)
    
    if not exists:
        raise HTTPException(status_code=400, detail="invalid or expired state")
    
    await api_state.redis.delete(key)

    tokens = await exchange_code_for_token(code)
    myself = await get_osu_self(tokens["access_token"])

    # enqueue the player to the redis queue for processing
    session_token = generate_session_token(myself["id"])
    await api_state.redis.lpush("pandemonium:player_queue", myself["id"])

    return {
        "success": True,
        "data": {
            "token": session_token,
            "player": myself
        }
    }