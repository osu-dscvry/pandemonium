from . import Base
from sqlalchemy import (
    Column, Integer, String, Float, JSON, Enum,
    ForeignKey, Index, BigInteger, TIMESTAMP
)
from .beatmaps import Mode
import enum

class Player(Base):
    __tablename__ = "players"

    id = Column(BigInteger, primary_key=True)
    username = Column(String(255), unique=True, index=True)
    country = Column(String(2))
    main_mode = Column(Enum(Mode, values_callable=lambda x: [e.value for e in Mode]))
    pp = Column(Float, default=0.0)
    rank = Column(Integer, default=0)
    country_rank = Column(Integer, default=0)
    joined_at = Column(Integer)  # timestamp
    last_synced_at = Column(Integer)  # timestamp
    settings = Column(JSON, default={})  # user-configurable discovery settings, etc.

class PlayerActivityType(enum.Enum):
    SCORE = "score"
    FAVOURITE = "favourite"
    PINNED = "pinned"
    NOMINATED = "nominated"

class PlayerActivity(Base):
    __tablename__ = "player_activity"

    id = Column(BigInteger, primary_key=True)
    player_id = Column(BigInteger, ForeignKey("players.id", ondelete="CASCADE"), index=True)
    type = Column(Enum(PlayerActivityType, values_callable=lambda x: [e.value for e in PlayerActivityType]))
    map_id = Column(BigInteger, nullable=True)
    mapset_id = Column(BigInteger, nullable=True)
    value = Column(JSON, default={})
    created_at = Column(TIMESTAMP)

    __table_args__ = (
        Index(
            "ix_player_activity_player_type_map_mapset",
            "player_id",
            "type",
            "map_id",
            "mapset_id",
            unique=True  # <-- make this unique for proper ON CONFLICT
        ),
    )
