import ossapi
import hashlib
import numpy
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

        if added_mapset:
            do_update = True
            print(f"Beatmapset {beatmapset.id} already exists in the database, checking if it should be updated...")

            if added_mapset.status != beatmapset.status.value:
                # the status is different
                do_update = True
            elif added_mapset.last_synced_at >= int(beatmapset.last_updated.timestamp()):
                do_update = False
                
                # it's safe to assume that generally, if the last sync time was 
                # after the set last updated, then it likely shouldn't be updated.
                # however, there are some circumstances where it shouldn't be
                beatmap_embeddings = await self.state.qdrant.retrieve(
                    collection_name="beatmap_embeddings",
                    ids=[bm.id for bm in beatmapset.beatmaps if beatmapset.beatmaps is not None],
                    with_payload=True,
                )

                # build a map of current beatmaps for quick lookup
                beatmap_by_id = {bm.id: bm for bm in (beatmapset.beatmaps or [])}

                for point in beatmap_embeddings:
                    pid = point.id
                    payload = getattr(point, "payload", {}) or {}
                    existing_tags = payload.get("user_tags", {})

                    bm = beatmap_by_id.get(pid)
                    if not bm:
                        continue

                    # normalize both sides to {str(tag_id): int(count)} for comparison
                    current_tags = {str(tag["tag_id"]): int(tag["count"]) for tag in (bm.top_tag_ids or [])}
                    existing_tags_norm = {str(k): int(v) for k, v in (existing_tags or {}).items()}

                    if current_tags != existing_tags_norm:
                        do_update = True
                        print(f"Detected tag differences for beatmap {pid}, marking set for update")
                        break

            if not do_update:
                print(f"Beatmapset {beatmapset.id} is up to date, skipping...")
                await session.close()
                await get_session.close()
                return

        if beatmapset.status is not RankStatus.RANKED:
            # temporarily ignore unranked maps

            # even though you can tag beatmaps, the statuses of them are
            # far too unpredictable to really trust the data for them
            await get_session.close()

            # TODO: delete maps that exist in the database and are
            # currently unranked
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
            "last_synced_at": int(datetime.utcnow().timestamp())
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
                "genre": beatmapset.genre["id"] if beatmapset.genre else 0,
                "language": beatmapset.language["id"] if beatmapset.language else 0,
                "creator": beatmapset.creator,
                "mode": beatmap.mode.value,
                "bpm": beatmap.bpm,
                "cs": beatmap.cs,
                "ar": beatmap.ar,
                "od": beatmap.accuracy,
                "hp": beatmap.drain,
                "tags": beatmapset.tags.split(),
                "user_tags": top_tags_payload,  # just ids + counts
                "play_count": beatmapset.play_count,
                "favourite_count": beatmapset.favourite_count,
                "status": beatmapset.status.value,
                "star_rating": beatmap.difficulty_rating,
                "length": beatmap.total_length,
                "max_combo": beatmap.max_combo,
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

    def compute_beatmap_embedding(self, beatmapset, beatmap):
        # numeric features (normalize)
        numeric = numpy.array([
            float(beatmap.difficulty_rating or 0) / 10.0,     # SR
            float(beatmap.bpm or 0) / 400.0,                  # BPM
            float(beatmap.total_length or 0) / 1500.0,        # total audio length
            float(beatmap.cs or 0) / 10.0,                    # CS
            float(beatmap.ar or 0) / 10.0,                    # AR
            float(beatmap.accuracy or 0) / 10.0,              # OD
            float(beatmap.drain or 0) / 10.0,                 # HP
            float(beatmap.hit_length or 0) / 1200.0,          # active drain time
        ], dtype=numpy.float32)

        # hashed tag bag
        TAG_DIM = 256
        TAG_WEIGHT = 4.0

        tag_vec = numpy.zeros(TAG_DIM, dtype=numpy.float32)

        for tag in (beatmap.top_tag_ids or []):
            t = tag["tag_id"]
            idx = hash_tag(t, TAG_DIM)
            tag_vec[idx] += 1.0

        # normalize tag block so counts don't distort direction
        if numpy.linalg.norm(tag_vec) > 0:
            tag_vec /= numpy.linalg.norm(tag_vec)

        emb = numpy.concatenate([
            numeric,
            TAG_WEIGHT * tag_vec,
        ])

        # pad to 512 dims
        if emb.shape[0] < 512:
            emb = numpy.pad(emb, (0, 512 - emb.shape[0]), mode='constant')

        return emb
    
def hash_tag(tag_id, dim=256):
    # stable integer hash
    h = int(hashlib.md5(str(tag_id).encode()).hexdigest(), 16)
    return h % dim