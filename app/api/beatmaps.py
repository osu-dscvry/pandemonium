import numpy
import math
from fastapi import APIRouter, HTTPException, Query, Depends
from .state import APIState, get_state
from qdrant_client.models import Filter,FieldCondition,Range,MatchValue
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database.beatmaps import Beatmap, BeatmapSet

router = APIRouter()

ALPHA_TAGS = 0.65      # tag overlap weight
BETA_META = 0.25       # metadata similarity weight
CANDIDATE_LIMIT = 1000  # candidacy limit for vectors

# -----------------------
# tags
def compute_tag_score(orig_payload, cand_payload, alpha):
    orig_tags_counts = orig_payload.get("user_tags", {})
    cand_tags_counts = cand_payload.get("user_tags", {})

    orig_tags = set(orig_tags_counts.keys())
    cand_tags = set(cand_tags_counts.keys())

    overlap_tags = orig_tags & cand_tags
    # weighted overlap: sum of minimum counts
    overlap_weight = sum(min(orig_tags_counts[t], cand_tags_counts[t]) for t in overlap_tags)

    # total weight: sum of all counts minus overlapping to avoid double-count
    total_weight = sum(orig_tags_counts.values()) + sum(cand_tags_counts.values()) - overlap_weight

    if total_weight == 0:
        return 0.0

    # apply exponential boost to overlap
    score = (overlap_weight ** alpha) / total_weight
    return min(score, 1.0)  # cap at 1.0


# -----------------------
# metadata

def star_bonus(sr1, sr2):
    diff = abs(sr1 - sr2)
    return math.exp(-diff * 1.5)

def length_bonus(l1, l2):
    diff = abs(l1 - l2)
    return math.exp(-diff / 5)


def meta_similarity(orig, cand):
    score = 0.0

    # mode / genre / language (binary)
    score += 0.05 * (orig["artist"] == cand["artist"])
    score += 0.05 * (orig["genre"] == cand["genre"])
    score += 0.05 * (orig["language"] == cand["language"])
    
    # numeric decay similarity functions
    def exp_decay_diff(v1, v2, scale=1.0):
        return math.exp(-abs(v1 - v2) / scale)
    
    if orig["mode"] == "mania" and cand["mode"] == "mania":
        # weigh cs before everything else as keymode
        score += 0.05 * (orig["cs"] == cand["cs"])
    else:
        score += 0.01 * (orig["cs"] == cand["cs"])

    score += 0.03 * star_bonus(orig["star_rating"], cand["star_rating"])
    score += 0.03 * length_bonus(orig["length"], cand["length"])
    score += 0.02 * exp_decay_diff(orig["bpm"], cand["bpm"], scale=10)
    score += 0.01 * exp_decay_diff(orig.get("max_combo", 0), cand.get("max_combo", 0), scale=50)

    # temporarily commented out as play count doesn't really measure
    # map similarity

    # score += 0.01 * exp_decay_diff(orig.get("play_count", 0), cand.get("play_count", 0), scale=1000)
    # score += 0.01 * exp_decay_diff(orig.get("favourite_count", 0), cand.get("favourite_count", 0), scale=100)

    return score


# ---------------------------
# final score

def total_similarity(orig, cand):
    tags = compute_tag_score(orig, cand, 2)
    meta = meta_similarity(orig, cand)

    score = (
        ALPHA_TAGS * tags +
        BETA_META  * meta
    )

    return min(max(score, 0.0), 1.0)  # clamp 0–1

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

    weighted_candidates = []

    for point in candidate_points:
        overall_score = total_similarity(
            vectors[0].payload,
            point.payload,
        )

        weighted_candidates.append((overall_score, point))
    
    weighted_candidates.sort(key=lambda x: x[0], reverse=True)

    
    for score, candidate in weighted_candidates:
        orig = vectors[0].payload
        cand = candidate.payload
        print(cand["title"])

        if "Painters" in cand["title"]:

            print(orig["user_tags"])
            print(cand["user_tags"])
            print(f"score: {score} - original: {orig["title"]} / candidate: {cand["title"]}")
            print("")

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
