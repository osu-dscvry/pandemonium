import httpx
import urllib.parse as urllib
from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import RedirectResponse
from .state import APIState, get_state
from app.util.api import get_current_user
from app.database.beatmaps import Beatmap, BeatmapSet
from app.database.players import Player, PlayerActivity
from sqlalchemy.ext.asyncio import AsyncSession
from qdrant_client.models import Filter,FieldCondition,Range,MatchValue
from sqlalchemy import select
import numpy as np

router = APIRouter()

weights = {
    "SCORE": 1.0,
    "FAVOURITE": 3.0,
    "PINNED": 5.0,
    "NOMINATED": 2.0,
}

@router.get("/feed/discovery")
async def get_discovery_feed(
    user: Player = Depends(get_current_user),
    limit: int = 50,
    state: APIState = Depends(get_state)
):
    session: AsyncSession = state.session_factory() # type: ignore
    activities = await session.execute(
        select(PlayerActivity.map_id, PlayerActivity.mapset_id)
        .where(PlayerActivity.player_id == user.id)
        .order_by(PlayerActivity.created_at.desc())
    )
    activities = activities.all()

    print(activities[0])

    beatmap_ids = [a[0] for a in activities if a[0] is not None]
    if not beatmap_ids:
        raise HTTPException(404, detail="no beatmap ids in player activity")

    # --- 3. retrieve embeddings from Qdrant ---
    vectors = await state.qdrant.retrieve(
        collection_name="beatmap_embeddings",
        ids=beatmap_ids,
        with_vectors=True
    )

    if not vectors:
        raise HTTPException(404, detail="no embeddings found for player activity")

    # --- 4. query Qdrant for similar beatmaps ---
    # aggregate top N results from all activity embeddings
    query_vectors = [v.vector for v in vectors if v.vector is not None]

    if not query_vectors:
        raise HTTPException(404, detail="all activity embeddings are empty")

    # batch query each vector and collect points
    seen_beatmapsets = set()
    top_beatmapsets = []
    filter_conditions = []

    filter_conditions.append(Filter(
        FieldCondition(key="mode", match=MatchValue(value=user.main_mode)),
    ))

    for qvec in query_vectors:
        response = await state.qdrant.query_points(
            collection_name="beatmap_embeddings",
            query=qvec,
            limit=limit,
            query_filter=Filter(must=filter_conditions),
        )

        for point in response.points:
            bmapset_id = int(point.payload["beatmapset_id"]) # type: ignore

            if bmapset_id not in seen_beatmapsets:
                seen_beatmapsets.add(bmapset_id)

                # fetch the beatmapset object
                result = await session.execute(
                    select(BeatmapSet)
                    .where(BeatmapSet.id == bmapset_id)
                    .limit(1)
                )

                beatmapset = result.scalar_one_or_none()
                if beatmapset:
                    # eagerly fetch beatmaps
                    await session.refresh(beatmapset, attribute_names=["beatmaps"])
                    top_beatmapsets.append(beatmapset)

            if len(top_beatmapsets) >= limit:
                break

        if len(top_beatmapsets) >= limit:
            break

    # --- 5. serialize for frontend ---
    return {"success": True, "data": top_beatmapsets }
