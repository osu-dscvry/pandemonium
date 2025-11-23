from . import Base
from sqlalchemy import (
    Column, Integer, String, Float, JSON, Enum,
    ForeignKey, Table, Index
)
from sqlalchemy.orm import relationship
import enum


# --- enums ----------------------------------------------------

class Mode(enum.StrEnum):
    STANDARD = "osu"
    TAIKO = "taiko"
    CATCH = "fruits"
    MANIA = "mania"

class BeatmapStatus(enum.IntEnum):
    GRAVEYARD = -2
    WIP = -1
    PENDING = 0
    RANKED = 1
    APPROVED = 2
    QUALIFIED = 3
    LOVED = 4

# --- models ----------------------------------------------------

class BeatmapSet(Base):
    __tablename__ = "beatmapsets"

    id = Column(Integer, primary_key=True)
    artist = Column(String(255))
    title = Column(String(255))
    creator = Column(String(255))
    source = Column(String(255))

    genre = Column(Integer)  # osu provides int, keep as-is
    language = Column(Integer)  # osu provides int, keep as-is
    tags = Column(JSON, default=list)  # from osu api
    status = Column(Integer) # enums are dumb because sqlalchemy expects a string or something

    play_count = Column(Integer, default=0)
    favourite_count = Column(Integer, default=0)
    last_synced_at = Column(Integer)

    beatmaps = relationship(
        "Beatmap",
        back_populates="beatmapset",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Beatmap(Base):
    __tablename__ = "beatmaps"

    id = Column(Integer, primary_key=True)
    beatmapset_id = Column(Integer, ForeignKey("beatmapsets.id", ondelete="CASCADE"))
    difficulty_name = Column(String(255))
    mode = Column(Enum(Mode, values_callable=lambda x: [e.value for e in Mode]))
    bpm = Column(Float)
    cs = Column(Float)
    ar = Column(Float)
    od = Column(Float)
    hp = Column(Float)
    star_rating = Column(Float)
    bpm = Column(Float)
    total_length = Column(Integer)  # in seconds
    hit_object_count = Column(Integer)
    approved_date = Column(Integer)  # timestamp

    extra_metadata = Column(JSON, default=dict)

    beatmapset = relationship("BeatmapSet", back_populates="beatmaps")

    __table_args__ = (
        Index("ix_beatmaps_beatmapset", "beatmapset_id"),
        Index("ix_beatmaps_mode", "mode"),
        Index("ix_beatmaps_star", "star_rating"),
        Index("ix_beatmaps_bpm", "bpm"),
    )