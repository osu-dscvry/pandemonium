import asyncio
import app.settings as settings

from abc import ABC, abstractmethod
from redis import asyncio as aioredis
from ossapi import OssapiAsync
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import VectorParams, Distance
from app.database import async_session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


class WorkerState:
    """
    A shared state object for workers to maintain context between tasks.
    """
    redis: aioredis.Redis
    osu: OssapiAsync
    qdrant: AsyncQdrantClient

    def __init__(self) -> None:
        self._engine = None
        self._sessionmaker = None
        pass

    async def init(self):
        self.redis = await aioredis.from_url(
            f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}",
            decode_responses=True
        )

        self.osu = OssapiAsync(
            settings.OSU_API_CLIENT_ID,
            settings.OSU_API_CLIENT_SECRET
        )
        
        self.qdrant = AsyncQdrantClient(url=settings.QDRANT_URL,api_key=settings.QDRANT_API_KEY)

        await self.qdrant.create_payload_index(
            collection_name="beatmap_embeddings",
            field_name="beatmapset_id",
            field_schema={"type": "integer"}  # string filterable
        )

    def get_engine(self):
        if self._engine is None:
            self._engine = create_async_engine(
                settings.PG_DSN,
                echo=False,
                future=True,
                pool_size=10,
                max_overflow=20,
            )
        return self._engine

    def get_sessionmaker(self):
        if self._sessionmaker is None:
            self._sessionmaker = async_sessionmaker(
                self.get_engine(),
                expire_on_commit=False,
                class_=AsyncSession,
            )
        return self._sessionmaker

    async def get_session(self):
        return self.get_sessionmaker()()
    
    async def get_redis_pool(self) -> aioredis.Redis:
        return await aioredis.from_url(
            f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}",
            decode_responses=True
        )

    async def close(self):
        if self.redis:
            await self.redis.close()

class Worker(ABC):
    """
    Abstract asynchronous worker class for processing tasks from a queue.
    All worker implementations must inherit from this class and implement the `process` method.
    """
    def __init__(self, queue_name: str, state: WorkerState):
        self.queue_name = queue_name
        self.state = state

    async def run(self):
        pool = await self.state.get_redis_pool()

        while True:
            item_id = await pool.lpop(self.queue_name)

            if item_id is not None:
                await self.process(item_id)
            else:
                await asyncio.sleep(1)  # sleep briefly if no items are available

    @abstractmethod
    async def process(self, item_id):
        raise NotImplementedError("Subclasses must implement this method")