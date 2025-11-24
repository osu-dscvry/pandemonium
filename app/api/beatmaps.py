import numpy
from fastapi import APIRouter, HTTPException, Query, Depends
from .state import APIState, get_state
from qdrant_client.models import Filter,FieldCondition,Range,MatchValue
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database.beatmaps import Beatmap, BeatmapSet

router = APIRouter()

ALPHA_TAGS = 0.65      # tag overlap weight
BETA_META = 0.25       # metadata similarity weight (unused for now)
CANDIDATE_LIMIT = 250  # candidacy limit for vectors

@router.get("/beatmapsets/{beatmapset_id}/similar")
async def get_similar_beatmapsets(
    beatmapset_id: int,
    state: APIState = Depends(get_state),

    limit: int = Query(10, le=50),
    mode: str = "osu"
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
    results = []

    for mapset_id in beatmapset_ids:
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
        "data": results
    }

@router.get("/beatmaps/{beatmap_id}/similar")
async def get_similar_beatmapsets_from_beatmap(
    beatmap_id: int,
    state: APIState = Depends(get_state),

    limit: int = Query(10, le=50)
):
    
    """
    Returns a list of beatmaps similar to the given beatmap.
    Ignores sets already seen by the user or in a provided ignore list.
    """
    session: AsyncSession = state.session_factory() # type: ignore

    result = await session.execute(select(Beatmap).where(Beatmap.id == beatmap_id))
    original = result.scalar_one_or_none()
    if not original:
        raise HTTPException(404, detail="beatmap not found")
    
    # retrieve the vector for the beatmap
    client = state.qdrant
    vectors = await client.retrieve(
        collection_name="beatmap_embeddings",
        ids=[original.id],
        with_vectors=True
    )
    if not vectors or vectors[0].vector is None:
        raise HTTPException(404, detail="embedding not found for beatmap")
    
    query_vector = vectors[0].vector

    response = await client.query_points(
        collection_name="beatmap_embeddings",
        query=query_vector,
        query_filter=Filter(
            must=[
                FieldCondition(key="mode", match=MatchValue(value=original.mode))
            ],
            must_not = [
                FieldCondition(key="beatmap_id", match=MatchValue(value=original.id)),
                FieldCondition(key="beatmapset_id", match=MatchValue(value=original.beatmapset_id))
            ]
        ),
        limit=CANDIDATE_LIMIT
    )

    
    candidate_points = response.points
    if not candidate_points:
        await session.close()
        return {"success": True, "data": []}

    # fetch Beatmap objects for weighting
    candidate_ids = [p.id for p in candidate_points]
    result = await session.execute(select(Beatmap).where(Beatmap.id.in_(candidate_ids)))

    def compute_tag_score(orig_payload, cand_payload):
        # both are dicts mapping tag_id -> count (or 1 if presence-only)
        orig_tags = set(orig_payload.get("user_tags", {}).keys())
        cand_tags = set(cand_payload.get("user_tags", {}).keys())

        print(orig_tags)
        overlap = len(orig_tags & cand_tags)
        total = len(orig_tags | cand_tags)
        return (overlap / total) if total > 0 else 0.0

    weighted_candidates = []
    for point in candidate_points:
        tag_score = compute_tag_score(vectors[0].payload, point.payload)
        overall_score = ALPHA_TAGS * tag_score
        weighted_candidates.append((overall_score, point))

        weighted_candidates.sort(key=lambda x: x[0], reverse=True)

    seen_mapsets = set()
    results = []

    for _, bm in weighted_candidates:
        mapset_id = bm.payload["beatmapset_id"]
        if mapset_id not in seen_mapsets:
            seen_mapsets.add(mapset_id)
            result = await session.execute(select(BeatmapSet).where(BeatmapSet.id == mapset_id))
            mapset = result.scalar_one_or_none()
            if mapset:
                # populate beatmaps in the set
                await session.refresh(mapset, attribute_names=["beatmaps"])
                results.append(mapset)
        if len(results) >= limit:
            break

    await session.close()
    return {"success": True, "data": results}