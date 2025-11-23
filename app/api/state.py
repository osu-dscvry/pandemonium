import app.settings as settings

from ossapi import OssapiAsync
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import VectorParams, Distance
from app.database import async_session
from sqlalchemy.orm import sessionmaker, Session

class APIState:
    session_factory: sessionmaker[Session]
    qdrant: AsyncQdrantClient
    osu: OssapiAsync

    async def init(self):
        self.session_factory = async_session
        self.osu = OssapiAsync(
            settings.OSU_API_CLIENT_ID,
            settings.OSU_API_CLIENT_SECRET
        )
        
        self.qdrant = AsyncQdrantClient(url=settings.QDRANT_URL,api_key=settings.QDRANT_API_KEY)
        pass

global_state = None

async def get_state():
    global global_state
    
    if global_state is None:
        global_state = APIState()
        await global_state.init()

        return global_state
    else:
        return global_state