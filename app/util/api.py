import jwt
import app.settings as settings
from fastapi import Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.state import get_state
from app.database.players import Player
from app.util import JWT_ALGORITHM

def verify_session_token(token: str):
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "invalid session token")

async def get_current_user(
    authorization: str = Header(...) # expects "Bearer <token>"
):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "invalid authorization header")

    state = await get_state()
    session: AsyncSession = state.session_factory() # type: ignore
    token = authorization.split(" ", 1)[1]
    payload = verify_session_token(token)
    user_id = int(payload["sub"])
    
    player = await session.get(Player, user_id)
    if not player:
        raise HTTPException(404, "user not found")
    
    await session.close()

    return player