from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import Optional
import logging
from datetime import datetime

from app.core.auth import get_current_user_optional
from app.core.database import run_query

logger = logging.getLogger(__name__)
router = APIRouter()


class RatingRequest(BaseModel):
    id: str
    isEffective: bool
    studentId: Optional[str] = None


def _article_query(extra_where: str = "", params: dict = None, limit: int = 10) -> list[dict]:
    where_clause = f"WHERE {extra_where}" if extra_where else ""
    results = run_query(
        f"""
        MATCH (a:Article)
        {where_clause}
        RETURN elementId(a) AS id,
               a.title AS title,
               a.full_description AS description,
               a.tags AS tags,
               a.url AS url,
               a.published_at AS created_at
        LIMIT $limit
        """,
        {**(params or {}), "limit": limit},
    )
    return results


# ── All articles (optionally filtered by module) ─────────────────────────────
@router.get("/api/tech-recommendations")
def get_tech_recommendations(
    limit: int = Query(10, ge=1, le=100),
    module: Optional[str] = Query(None),
):
    try:
        if module:
            results = run_query(
                """
                MATCH (a:Article)-[:RELATED_TO]->(m:Module)
                WHERE toLower(m.name) = toLower($module)
                   OR toLower(m.name) CONTAINS toLower($module)
                RETURN a.id AS id, a.title AS title,
                       a.description AS description, a.tags AS tags,
                       a.url AS url, a.published_at AS created_at
                LIMIT $limit
                """,
                {"module": module, "limit": limit},
            )
            return results
        return _article_query(limit=limit)
    except Exception as e:
        logger.error(f"Error fetching tech recommendations: {e}")
        raise HTTPException(status_code=503, detail={"status": "error", "message": "Failed to fetch recommendations"})


# ── Single article ───────────────────────────────────────────────────────────
@router.get("/api/tech-recommendations/trending")
def get_trending(limit: int = Query(3, ge=1, le=20)):
    try:
        return _article_query(limit=limit)
    except Exception as e:
        logger.error(f"Error fetching trending: {e}")
        raise HTTPException(status_code=503, detail={"status": "error", "message": "Failed to fetch trending"})


@router.get("/api/tech-recommendations/diagnostics")
def diagnostics():
    try:
        result = run_query("MATCH (n) RETURN count(n) AS total")
        article_result = run_query("MATCH (a:Article) RETURN a LIMIT 1")
        sample_props = list(dict(article_result[0]["a"]).keys()) if article_result else []
        return {
            "connection": {"status": "connected", "node_count": result[0]["total"]},
            "schema": {"sample_properties": sample_props},
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Diagnostics error: {e}")
        raise HTTPException(status_code=503, detail={"status": "error", "message": str(e)})


@router.get("/api/tech-recommendations/by-tags")
def get_by_tags(tags: str = Query(..., description="Comma-separated tags")):
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    if not tag_list:
        raise HTTPException(status_code=400, detail={"status": "error", "message": "No tags provided"})
    try:
        results = run_query(
            """
            MATCH (a:Article)
            WHERE any(tag IN a.tags WHERE tag IN $tags)
            RETURN elementId(a) AS id,
                   a.title AS title,
                   a.full_description AS description,
                   a.tags AS tags,
                   a.url AS url,
                   a.published_at AS created_at
            ORDER BY a.published_at DESC
            LIMIT 10
            """,
            {"tags": tag_list},
        )
        return results
    except Exception as e:
        logger.error(f"Error fetching by tags: {e}")
        raise HTTPException(status_code=503, detail={"status": "error", "message": "Failed to fetch by tags"})


@router.get("/api/tech-recommendations/{recommendation_id}")
def get_tech_recommendation(recommendation_id: str):
    try:
        result = run_query(
            """
            MATCH (a:Article) WHERE elementId(a) = $id
            RETURN elementId(a) AS id,
                   a.title AS title,
                   a.full_description AS description,
                   a.tags AS tags,
                   a.url AS url,
                   a.published_at AS created_at
            """,
            {"id": recommendation_id},
        )
        if not result:
            raise HTTPException(status_code=404, detail={"status": "error", "message": "Recommendation not found"})
        return result[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching recommendation {recommendation_id}: {e}")
        raise HTTPException(status_code=503, detail={"status": "error", "message": "Failed to fetch recommendation"})


@router.post("/api/tech-recommendations/rating")
def rate_recommendation(
    body: RatingRequest,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    try:
        # Aggregate rating on the ArticleRating node
        run_query(
            """
            MERGE (r:ArticleRating {articleId: $id})
            ON CREATE SET r.effective = 0, r.not_effective = 0
            SET r.effective = CASE WHEN $effective THEN r.effective + 1 ELSE r.effective END,
                r.not_effective = CASE WHEN NOT $effective THEN r.not_effective + 1 ELSE r.not_effective END
            """,
            {"id": body.id, "effective": body.isEffective},
        )
        # Per-student RATED relationship (persistent, fixes I-3)
        student_id = (current_user or {}).get("sub") or body.studentId
        if student_id:
            run_query(
                """
                MATCH (s:Student {id: $sid}), (a:Article {id: $aid})
                MERGE (s)-[r:RATED]->(a)
                SET r.effective = $effective, r.ratedAt = $ts
                """,
                {
                    "sid": student_id,
                    "aid": body.id,
                    "effective": body.isEffective,
                    "ts": datetime.now().isoformat(),
                },
            )
        return {"success": True}
    except Exception as e:
        logger.error(f"Error rating recommendation: {e}")
        raise HTTPException(status_code=503, detail={"status": "error", "message": "Failed to process rating"})
