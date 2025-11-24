from enum import IntFlag, auto
from sqlalchemy import Column, Integer, String, Table, ForeignKey
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import relationship
from . import Base

# association table
user_groups = Table(
    "user_groups",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("players.id", ondelete="CASCADE")),
    Column("group_id", Integer, ForeignKey("groups.id", ondelete="CASCADE")),
)

class Permissions(IntFlag):
    # global permissions: accessing your own feed, login, etc
    # read permissions
    VIEW_OTHERS_FEED        = auto()  # can access the discovery feeds of others
    VIEW_PLAYERS            = auto()  # can query player info

    # write / modify
    CURATE_TAGS             = auto() # the curation of tags (assignign them)
    MANAGE_TAGS             = auto() # allows the creation or deletion of tags
    MANAGE_MAPSETS          = auto() # allows the creation, deletion or manual enqueue of beatmaps/beatmapsets
    MANAGE_PLAYERS          = auto() # allows for creation, deletion or manual enqueue of players

    # administration
    MANAGE_USERS            = auto()
    MANAGE_GROUPS           = auto()

class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, index=True) # e.g, "admin", "moderator", "curator"
    description = Column(String(255), nullable=True)
    permissions = Column(Integer, default=0)

    members = relationship("Player", secondary=user_groups, back_populates="groups")


async def populate_groups_table(session: AsyncSession):
    """Insert or update default groups with appropriate permission masks.

    This performs an UPSERT for each named group using its `name` as
    the conflict target.
    """

    groups = [
        {
            "name": "ADMIN",
            "description": "Full access to manage the site",
            "permissions": int(
                Permissions.VIEW_OTHERS_FEED
                | Permissions.VIEW_PLAYERS
                | Permissions.MANAGE_TAGS
                | Permissions.MANAGE_MAPSETS
                | Permissions.MANAGE_PLAYERS
                | Permissions.MANAGE_USERS
                | Permissions.MANAGE_GROUPS
                | Permissions.CURATE_TAGS
            ),
        },
        {
            "name": "MODERATOR",
            "description": "Moderation and content management",
            "permissions": int(
                Permissions.VIEW_OTHERS_FEED
                | Permissions.VIEW_PLAYERS
                | Permissions.MANAGE_TAGS
                | Permissions.MANAGE_MAPSETS
                | Permissions.CURATE_TAGS
            ),
        },
        {
            "name": "CURATOR",
            "description": "Can manage mapsets and curate content",
            "permissions": int(
                Permissions.VIEW_OTHERS_FEED
                | Permissions.CURATE_TAGS
            ),
        }
    ]

    for g in groups:
        stmt = insert(Group).values(**g)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Group.name],
            set_=g,
        )

        await session.execute(stmt)

    await session.commit()