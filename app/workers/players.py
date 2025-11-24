import ossapi
from . import Worker, WorkerState
from datetime import datetime
from app.database.players import Player, PlayerActivity, PlayerActivityType
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from qdrant_client.http.models import PointStruct
from ossapi import Mod
from typing import Any

class PlayerWorker(Worker):
    def __init__(self, state: WorkerState):
        super().__init__("pandemonium:player_queue", state)

    async def process(self, item_id):
        """
        Processes a single player ID:
        - fetches player info from osu! API
        - updates the players table
        - updates the PlayerActivity table (scores, favorites, pinned)
        """
        osu = self.state.osu
        session = await self.state.get_session()
        pool = await self.state.get_redis_pool()
        activities = []

        player = await osu.user(item_id, mode="osu")
        print(f"Processing player: {player.username} (ID: {player.id})")

        if player.is_bot:
            print(f"Skipping bot player: {player.username} (ID: {player.id})")
            return

        player_values = {
            "id": player.id,
            "username": player.username,
            "country": player.country.code,
            "main_mode": player.playmode,
            "pp": player.statistics.pp if hasattr(player, "statistics") else 0.0,
            "rank": player.statistics.global_rank if hasattr(player, "statistics") else 0,
            "country_rank": player.statistics.country_rank if hasattr(player, "statistics") else 0,
            "joined_at": int(player.join_date.timestamp()) if player.join_date else None,
            "last_synced_at": int(datetime.utcnow().timestamp()),
        }

        stmt = insert(Player).values(**player_values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Player.id],
            set_=player_values
        )

        await session.execute(stmt)

        activities = []

        def make_activity(type_str, map_id=None, mapset_id=None, value={}):
            return {
                "player_id": player.id,
                "type": type_str,
                "map_id": map_id,
                "mapset_id": mapset_id,
                "value": value,
                "created_at": datetime.utcnow()
            }

        for favourite in await osu.user_beatmaps(player.id, type="favourite"):
            activities.append(make_activity(
                PlayerActivityType.FAVOURITE.value,
                mapset_id=favourite.id,
            ))


        for mode in ["osu"]: # standard only for now -- , "taiko", "fruits", "mania"
            await self.process_player(player, mode, activities)
            
        # batch upsert
        for act in activities:
            # enqueue for beatmap processing if mapset_id is present
            if act["mapset_id"]:
                qname = "pandemonium:beatmap_queue"
                member = str(act["mapset_id"]) if act["mapset_id"] is not None else None
                if member is not None:
                    found = False
                    try:
                        pos = await pool.lpos(qname, member)
                        if pos is not None:
                            found = True
                    except Exception:
                        # LPOS may not be supported by older redis clients/servers; fallback to LRANGE
                        existing = await pool.lrange(qname, 0, -1)
                        if member in existing:
                            found = True

                    if not found:
                        print(f"Enqueuing beatmapset {member} for processing due to player activity.")
                        await pool.rpush(qname, member)
                    else:
                        print(f"Beatmapset {member} already enqueued, skipping")

            stmt = insert(PlayerActivity).values(**act)
            stmt = stmt.on_conflict_do_update(
                index_elements=[PlayerActivity.player_id, PlayerActivity.type, PlayerActivity.map_id, PlayerActivity.mapset_id],
                set_={"value": act["value"], "created_at": act["created_at"]}
            )
            await session.execute(stmt)

        await session.commit()
        await session.close()
        await pool.close() # close
        
        pass

    async def process_player(self, player: ossapi.User, mode: str, activities: list):
        osu = self.state.osu
        
        def make_activity(type_str, map_id=None, mapset_id=None, value={}):
            return {
                "player_id": player.id,
                "type": type_str,
                "map_id": map_id,
                "mapset_id": mapset_id,
                "value": value,
                "created_at": datetime.utcnow()
            }

        for top_score in await osu.user_scores(player.id, type="best", limit=200, mode=mode):
            activities.append(make_activity(
                PlayerActivityType.SCORE.value,
                map_id=top_score.beatmap_id,
                mapset_id=top_score.beatmap.beatmapset_id if top_score.beatmap is not None else None,
                value={
                    "mode": top_score.ruleset_id,
                    "score": top_score.total_score,
                    "pp": top_score.pp,
                    "rank": top_score.rank.value,
                    "mods": self._serialize_mods(top_score.mods),
                }
            ))

        for recent_score in await osu.user_scores(player.id, type="recent", limit=100, mode=mode):
            activities.append(make_activity(
                PlayerActivityType.SCORE.value,
                map_id=recent_score.beatmap_id,
                mapset_id=recent_score.beatmap.beatmapset_id if recent_score.beatmap is not None else None,
                value={
                    "mode": recent_score.ruleset_id,
                    "score": recent_score.total_score,
                    "pp": recent_score.pp,
                    "rank": recent_score.rank.value,
                    "mods": self._serialize_mods(recent_score.mods),
                }
            ))

    def _serialize_mods(self, mods: Any):
        if not mods:
            return []
        # ossapi mods list may contain NonLegacyMod objects or legacy mod ints/strings
        normalized = []
        for m in mods:
            ac = getattr(m, "acronym", None)
            if ac:
                normalized.append(str(ac))
                continue
            
            if isinstance(m, str):
                normalized.append(m)
                continue
            
            # final fallback — make it string
            normalized.append(str(m))

        # ensure no duplicates, keep order
        return list(dict.fromkeys(normalized))

