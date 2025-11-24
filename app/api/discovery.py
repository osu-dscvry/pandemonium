from fastapi import APIRouter, HTTPException, Query, Depends
from .state import APIState, get_state
from app.util.api import get_current_user
from app.database.beatmaps import Beatmap, BeatmapSet
from app.database.players import Player, PlayerActivity
from sqlalchemy.ext.asyncio import AsyncSession
from qdrant_client.models import Filter,FieldCondition,QueryRequest,MatchValue,ScoredPoint
from sqlalchemy import select
from itertools import chain

router = APIRouter()

ACTIVITY_WEIGHTS = {
    "score": 1.0,
    "favourite": 1.8,
    "pinned": 2.8,
    "nominated": 1.6,
}

ALPHA_TAGS = 0.65     # tag overlap strength
BETA_META = 0.25      # metadata similarity strength
CANDIDATE_LIMIT = 50  # candidacy limit for vectors

@router.get("/feed/discovery")
async def get_discovery_feed(
    user: Player = Depends(get_current_user),
    state: APIState = Depends(get_state),

    # parameters
    limit: int = 50,
    mode: str = Query(None, description="Filter by osu! mode, e.g., 'osu', 'taiko', 'catch', 'mania'")
):
    session: AsyncSession = state.session_factory() # type: ignore
    activity_rows = await session.execute(
        select(
            PlayerActivity.type,
            PlayerActivity.map_id,
            PlayerActivity.mapset_id
        )
        .where(PlayerActivity.player_id == user.id)
        .order_by(PlayerActivity.created_at.desc())
    )

    activity_rows = activity_rows.all()

    if not activity_rows:
        raise HTTPException(404, "no activity found for this user")
    
    activity_weight_map: dict[int, float] = {}
    beatmap_ids: set[int] = set()

    for activity_type, map_id, mapset_id in activity_rows:
        # compute weights now (mapset-level)
        # derive mapset id if only map_id exists
        if mapset_id is None and map_id is not None:
            r = await session.execute(
                select(Beatmap.beatmapset_id).where(Beatmap.id == map_id)
            )
            mapset_id = r.scalar_one_or_none()

        if mapset_id is None:
            continue

        # accumulate activity weights
        w = ACTIVITY_WEIGHTS.get(activity_type, 1.0)
        activity_weight_map[mapset_id] = activity_weight_map.get(mapset_id, 1.0) + w

        # now expand into beatmaps for embedding usage
        if map_id is not None:
            # direct beatmap activity: just add map_id
            beatmap_ids.add(map_id)
        else:
            # mapset-only activity: expand entire set
            r = await session.execute(
                select(Beatmap.id).where(Beatmap.beatmapset_id == mapset_id)
            )

            ids = [bm_id for (bm_id,) in r.all()]
            beatmap_ids.update(ids)

    vectors = await state.qdrant.retrieve(
        collection_name="beatmap_embeddings",
        ids=list(beatmap_ids),
        with_vectors=True
    )

    query_vectors = [v.vector for v in vectors if v.vector is not None]

    if not query_vectors:
        raise HTTPException(500, "no candidates available for user activity. this is a server mistake, so if you get this error please report it!")
    
    filter_must = []
    candidate_points: list[ScoredPoint] = []

    if mode:
        filter_must.append(FieldCondition(key="mode", match=MatchValue(value=mode)))
    else:
        filter_must.append(FieldCondition(key="mode", match=MatchValue(value=user.main_mode)))

    q_filter = Filter(must=filter_must)
    responses = await state.qdrant.query_batch_points(
        collection_name="beatmap_embeddings",
        requests=[
            QueryRequest(
                query=vector,
                filter=q_filter,
                limit=CANDIDATE_LIMIT,
                with_payload=True
            ) for vector in query_vectors
        ]
    )

    candidate_points = list(chain.from_iterable(resp.points for resp in responses))

    if not candidate_points:
        raise HTTPException(500, "no candidates available for user activity. this is a server mistake, so if you get this error please report it!")
    
    scored_mapsets = {}

    for point in candidate_points:
        mapset_id = int(point.payload["beatmapset_id"])
        base_sim = point.score

        activity_w = activity_weight_map.get(mapset_id, 1.0)
        final_score = base_sim * activity_w

        # keep the best-scoring sample for each mapset
        if mapset_id not in scored_mapsets or final_score > scored_mapsets[mapset_id][0]:
            scored_mapsets[mapset_id] = (final_score, point)

    sorted_mapsets = sorted(
        scored_mapsets.items(),
        key=lambda x: x[1][0],
        reverse=True
    )
    sorted_mapsets = sorted_mapsets[:limit]
    results = []

    for mapset_id, _ in sorted_mapsets:
        result = await session.execute(
            select(BeatmapSet).where(BeatmapSet.id == mapset_id)
        )

        mapset = result.scalar_one_or_none()
        if mapset:
            # fetch beatmaps
            await session.refresh(mapset, attribute_names=["beatmaps"])
            results.append(mapset)
    
    await session.close()

    return {
        "success": True,
        "data": results,
    }


    

