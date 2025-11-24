import numpy
from fastapi import APIRouter, HTTPException, Query, Depends
from .state import APIState, get_state
from qdrant_client.models import Filter,FieldCondition,Range,MatchValue
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database.beatmaps import Beatmap

router = APIRouter()

@router.get("/beatmapsets/{beatmapset_id}/similar")
async def get_similar_beatmapsets(
    beatmapset_id: int,
    limit: int = Query(10, le=50),
    mode: str = "osu",
    state: APIState = Depends(get_state)
):
    """
    Returns a list of beatmapsets similar to the given beatmapset.
    """
    session: AsyncSession = state.session_factory() # type: ignore

    # get every beatmap in the set
    beatmaps = await session.execute(
        select(Beatmap).where(Beatmap.beatmapset_id == beatmapset_id)
    )
    beatmaps = beatmaps.scalars().all()

    if len(beatmaps) == 0:
        raise HTTPException(status_code=404, detail="beatmapset not found")

    client = state.qdrant
    filter_conditions = []
    vectors = await client.retrieve(
        collection_name="beatmap_embeddings",
        ids=[bm.id for bm in beatmaps],
        with_vectors=True
    )

    filter_conditions.append(
        FieldCondition(key="mode", match=MatchValue(value=mode)),
    )

    response = await client.query_points(
        collection_name="beatmap_embeddings",
        query=numpy.mean([v.vector for v in vectors], axis=0).tolist(),
        query_filter=Filter(must=filter_conditions,must_not=[
            FieldCondition(key="beatmapset_id", match=MatchValue(value=beatmapset_id))
        ]),
        limit=limit
    )
    
    beatmapset_ids = list({p.payload["beatmapset_id"] for p in response.points})

    await session.close()
    
    return {
        "success": True,
        "data": beatmapset_ids
    }
