import ossapi
import hashlib
from . import Worker, WorkerState
from ossapi.enums import RankStatus
from app.logger import worker_logger as logger
from datetime import datetime
from app.database.beatmaps import BeatmapSet, Beatmap
from sqlalchemy.dialects.postgresql import insert
from qdrant_client.http.models import PointStruct
 
class BeatmapWorker(Worker):
    def __init__(self, state: WorkerState):
        super().__init__("pandemonium:beatmap_queue", state)

    async def process(self, item_id):
        # Implement the processing logic for beatmap items here
        beatmapset = await self.state.osu.beatmapset(item_id)
        session = await self.state.get_session()
        get_session = await self.state.get_session()
        points = []

        print(f"Processing beatmapset {beatmapset.id} - {beatmapset.artist} - {beatmapset.title}")

        # get the beatmapset from the database, or create it if it doesn't exist
        added_mapset = await get_session.get(BeatmapSet, beatmapset.id)

        if beatmapset.status is not RankStatus.RANKED:
            # temporarily ignore unranked maps

            # even though you can tag beatmaps, the statuses of them are
            # far too unpredictable to really trust the data for them
            await get_session.close()

            # TODO: delete maps that exist in the database and are
            # currently unranked
            return

        if added_mapset:
            do_update = True
            print(f"Beatmapset {beatmapset.id} already exists in the database, checking if it should be updated...")

            if added_mapset.status == beatmapset.status.value:
                do_update = False
            #elif added_mapset.last_synced_at >= int(beatmapset.last_updated.timestamp()) # TODO: check if tags updated

            if not do_update:
                print(f"Beatmapset {beatmapset.id} is up to date, skipping...")
                await session.close()
                await get_session.close()
                return
            
        await get_session.close()
        values = {
            "id": beatmapset.id,
            "artist": beatmapset.artist,
            "title": beatmapset.title,
            "creator": beatmapset.creator,
            "source": beatmapset.source,
            "genre": beatmapset.genre["id"] if beatmapset.genre else 0,
            "language": beatmapset.language["id"] if beatmapset.language else 0,
            "tags": beatmapset.tags.split(),
            "status": beatmapset.status.value,
            "play_count": beatmapset.play_count,
            "favourite_count": beatmapset.favourite_count,
            "last_synced_at": int(datetime.utcnow().timestamp()),
        }

        stmt = insert(BeatmapSet)
        stmt = stmt.values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[BeatmapSet.id],
            set_=values
        )
            
        await session.execute(stmt)

        for beatmap in beatmapset.beatmaps or []:
            embedding = self.compute_beatmap_embedding(beatmapset, beatmap)
            top_tags_payload = {str(tag["tag_id"]): tag["count"] for tag in beatmap.top_tag_ids or []} # type: ignore // the ossapi types are wrong
            bm_values  = {
                "id": beatmap.id,
                "beatmapset_id": beatmapset.id,
                "difficulty_name": beatmap.version,
                "mode": beatmap.mode.value,
                "bpm": beatmap.bpm,
                "cs": beatmap.cs,
                "ar": beatmap.ar,
                "od": beatmap.accuracy,
                "hp": beatmap.drain,
                "star_rating": beatmap.difficulty_rating,
                "total_length": beatmap.total_length,
                "hit_object_count": beatmap.hit_length,
                "extra_metadata": {
                    "max_combo": beatmap.max_combo,
                }
            }

            stmt = insert(Beatmap)
            stmt = stmt.values(**bm_values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Beatmap.id],
                set_=bm_values
            )

            print(f"Upserted beatmap {beatmap.id} - {beatmapset.artist} - {beatmapset.title} [{beatmap.version}]")

            await session.execute(stmt)

            payload = {
                "beatmapset_id": beatmapset.id,
                "beatmap_id": beatmap.id,
                "title": beatmapset.title,
                "artist": beatmapset.artist,
                "creator": beatmapset.creator,
                "mode": beatmap.mode.value,
                "genre": beatmapset.genre["id"] if beatmapset.genre else 0,
                "language": beatmapset.language["id"] if beatmapset.language else 0,
                "tags": beatmapset.tags.split(),
                "user_tags": top_tags_payload,  # just ids + counts
                "play_count": beatmapset.play_count,
                "favourite_count": beatmapset.favourite_count,
                "status": beatmapset.status.value,
                "star_rating": beatmap.difficulty_rating,
                "length": beatmap.total_length,
                "embed_version": 1,
            }
            
            points.append(PointStruct(id=beatmap.id, vector=embedding, payload=payload))
            print(f"Prepared embedding point for beatmap {beatmap.id} - {beatmapset.artist} - {beatmapset.title} [{beatmap.version}]")

        await session.commit()
        await session.close()
        await self.state.qdrant.upsert(
            collection_name="beatmap_embeddings",
            points=points
        )

        print(f"Completed processing beatmapset {beatmapset.id} - {beatmapset.artist} - {beatmapset.title}")

        pass

    def compute_beatmap_embedding(self, beatmapset: ossapi.Beatmapset, beatmap: ossapi.Beatmap):
        vector = [
            float(beatmap.difficulty_rating or 0) / 10.0,
            float(beatmap.bpm or 0) / 300.0,
            float(beatmap.total_length or 0) / 600.0,
            float(beatmap.cs or 0) / 10.0,
            float(beatmap.ar or 0) / 10.0,
            float(beatmap.accuracy or 0) / 10.0,
            float(beatmap.drain or 0) / 10.0,
            float(beatmap.hit_length or 0) / 2000.0,
        ]
        
        # encode top N user tags
        TAG_SCALE = 1000.0  # dominates everything else
        top_n = 20
        for i in range(top_n):
            if i < len(beatmap.top_tag_ids or []):
                tag = beatmap.top_tag_ids[i]
                vector.append(TAG_SCALE * tag_to_float(tag["tag_id"])) # type: ignore
            else:
                vector.append(0.0)

        while len(vector) < 512:
            vector.append(0.0)
            
        return vector
    
def tag_to_float(tag_id: int) -> float:
    # deterministic hash to [0,1)
    h = hashlib.md5(str(tag_id).encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF