from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
import app.settings as settings

engine = create_async_engine(
    settings.PG_DSN,
    echo=False,
    future=True,
    pool_size=10,
    max_overflow=20
)

async_session = sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

Base = declarative_base()