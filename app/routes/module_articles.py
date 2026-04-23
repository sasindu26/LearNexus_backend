import re
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
import logging

from app.core.database import run_query

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_articles_for_module(module_name: str) -> list[dict]:
    results = run_query(
        """
        MATCH (a:Article)-[:RELATED_TO]->(m:Module)
        WHERE toLower(m.name) = toLower($name) OR toLower(m.name) CONTAINS toLower($name)
        RETURN a.elementId AS id,
               a.title AS title,
               a.full_description AS description,
               a.tags AS tags,
               a.url AS url,
               a.published_at AS created_at
        """,
        {"name": module_name},
    )
    if not results:
        results = run_query(
            """
            MATCH (a:Article)
            WHERE any(tag IN a.tags WHERE toLower(tag) CONTAINS toLower($name))
               OR toLower(a.title) CONTAINS toLower($name)
            RETURN a.elementId AS id,
                   a.title AS title,
                   a.full_description AS description,
                   a.tags AS tags,
                   a.url AS url,
                   a.published_at AS created_at
            LIMIT 10
            """,
            {"name": module_name},
        )
    return results


def _relevance_score(article: dict, module_name: str) -> int:
    score = 0
    name_lower = module_name.lower()
    if name_lower in (article.get("title") or "").lower():
        score += 10
    for tag in article.get("tags") or []:
        if name_lower in tag.lower():
            score += 5
    desc = (article.get("description") or "").lower()
    if name_lower in desc:
        score += 3 + min(len(re.findall(r"\b" + re.escape(name_lower) + r"\b", desc)), 5)
    return score


@router.get("/api/module-articles")
def get_module_articles(
    module: str = Query(..., description="Module name"),
    limit: int = Query(10, ge=1, le=50),
):
    try:
        articles = _get_articles_for_module(module)
        for a in articles:
            a["relevance_score"] = _relevance_score(a, module)
        articles.sort(key=lambda x: x["relevance_score"], reverse=True)
        return articles[:limit]
    except Exception as e:
        logger.error(f"Error fetching module articles for '{module}': {e}")
        raise HTTPException(status_code=503, detail={"status": "error", "message": "Failed to fetch module articles"})
