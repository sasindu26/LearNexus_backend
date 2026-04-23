from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from urllib.parse import unquote
import logging

from app.core.database import run_query

logger = logging.getLogger(__name__)
router = APIRouter()


class TopicItem(BaseModel):
    id: str
    name: str
    title: str
    description: str
    isCompleted: bool = False
    subtopics: list = []
    difficulty: str = "beginner"
    timeEstimate: str = "15 mins"
    progress: int = 0


class TopicsPostBody(BaseModel):
    moduleName: Optional[str] = None
    module_name: Optional[str] = None


def _fetch_topics(module_name: str) -> list[dict]:
    decoded = unquote(module_name)
    results = run_query(
        """
        MATCH (m:Module)-[:HAS_TOPIC]->(t:Topic)
        WHERE toLower(m.name) = toLower($name) OR toLower(m.name) CONTAINS toLower($name)
        RETURN t.name AS topic, t.description AS description
        """,
        {"name": decoded},
    )
    return [
        TopicItem(
            id=f"topic-{idx}",
            name=row.get("topic") or "Unnamed Topic",
            title=row.get("topic") or "Unnamed Topic",
            description=row.get("description") or "No description available",
        ).model_dump()
        for idx, row in enumerate(results)
    ]


@router.get("/module-content/{module_name}/topics")
def get_topics_by_module_content(module_name: str):
    try:
        return _fetch_topics(module_name)
    except Exception as e:
        logger.error(f"Error fetching topics for '{module_name}': {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": "Failed to fetch topics"})


@router.get("/modules/{module_name}/topics")
def get_topics_by_modules(module_name: str):
    try:
        return _fetch_topics(module_name)
    except Exception as e:
        logger.error(f"Error fetching topics for '{module_name}': {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": "Failed to fetch topics"})


@router.post("/module-content/{module_name}/topics")
def post_topics_by_module_content(module_name: str, body: Optional[TopicsPostBody] = None):
    resolved = (body.moduleName or body.module_name or module_name) if body else module_name
    try:
        return _fetch_topics(resolved)
    except Exception as e:
        logger.error(f"Error fetching topics for '{resolved}': {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": "Failed to fetch topics"})


@router.post("/modules/{module_name}/topics")
def post_topics_by_modules(module_name: str, body: Optional[TopicsPostBody] = None):
    resolved = (body.moduleName or body.module_name or module_name) if body else module_name
    try:
        return _fetch_topics(resolved)
    except Exception as e:
        logger.error(f"Error fetching topics for '{resolved}': {e}")
        raise HTTPException(status_code=500, detail={"status": "error", "message": "Failed to fetch topics"})
