"""
S-6 FR-07: Engagement logging and score computation.

Events tracked:
  login          weight 1
  chat_message   weight 3
  module_view    weight 2
  article_view   weight 1
  module_complete weight 5

Engagement score = sum(weight * events) over last 14 days, normalised to 0-100.
Risk tiers: high ≥60, medium 30-59, low <30 (used by S-7 AAE).
"""

import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.core.database import run_query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/engagement", tags=["engagement"])

VALID_EVENTS = {"login", "chat_message", "module_view", "article_view", "module_complete"}

EVENT_WEIGHTS = {
    "login": 1,
    "chat_message": 3,
    "module_view": 2,
    "article_view": 1,
    "module_complete": 5,
}

MAX_SCORE = 14 * (3 * 3 + 2 * 2 + 1)   # theoretical max per 14-day window → normalise against this


class LogRequest(BaseModel):
    event_type: str
    metadata: Optional[dict] = None


@router.post("/log", status_code=status.HTTP_201_CREATED)
def log_event(body: LogRequest, current_user: dict = Depends(get_current_user)):
    if body.event_type not in VALID_EVENTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"status": "error", "message": f"Unknown event type '{body.event_type}'"},
        )

    student_id = current_user["sub"]
    log_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    meta_str = str(body.metadata or {})

    run_query(
        """
        MATCH (s:Student {id: $sid})
        CREATE (e:EngagementLog {
            id: $lid,
            eventType: $etype,
            timestamp: $ts,
            metadata: $meta
        })
        CREATE (s)-[:HAS_LOG]->(e)
        """,
        {"sid": student_id, "lid": log_id, "etype": body.event_type, "ts": now, "meta": meta_str},
    )
    return {"status": "success", "logId": log_id}


@router.get("/score")
def get_engagement_score(current_user: dict = Depends(get_current_user)):
    student_id = current_user["sub"]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()

    rows = run_query(
        """
        MATCH (s:Student {id: $sid})-[:HAS_LOG]->(e:EngagementLog)
        WHERE e.timestamp >= $cutoff
        RETURN e.eventType AS event_type, count(e) AS cnt
        """,
        {"sid": student_id, "cutoff": cutoff},
    )

    weighted_sum = sum(
        EVENT_WEIGHTS.get(r["event_type"], 1) * r["cnt"] for r in rows
    )
    score = min(100, round(weighted_sum / MAX_SCORE * 100, 1)) if MAX_SCORE > 0 else 0
    risk = "high" if score >= 60 else "medium" if score >= 30 else "low"

    event_counts = {r["event_type"]: r["cnt"] for r in rows}

    return {
        "status": "success",
        "score": score,
        "risk_tier": risk,
        "events_last_14_days": event_counts,
        "total_events": sum(event_counts.values()),
    }


@router.get("/history")
def get_engagement_history(
    days: int = 30,
    current_user: dict = Depends(get_current_user),
):
    student_id = current_user["sub"]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    rows = run_query(
        """
        MATCH (s:Student {id: $sid})-[:HAS_LOG]->(e:EngagementLog)
        WHERE e.timestamp >= $cutoff
        RETURN e.eventType AS event_type, e.timestamp AS ts
        ORDER BY e.timestamp DESC
        LIMIT 200
        """,
        {"sid": student_id, "cutoff": cutoff},
    )

    return {
        "status": "success",
        "history": [{"event_type": r["event_type"], "timestamp": r["ts"]} for r in rows],
    }
