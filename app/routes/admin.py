"""
Admin endpoints — not exposed in public docs (no auth for now; add API-key middleware in prod).
"""

import logging
from fastapi import APIRouter, BackgroundTasks

from app.core.database import run_query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/aae/trigger")
def trigger_aae(background_tasks: BackgroundTasks):
    """Manually trigger an AAE check run (runs in background)."""
    from app.services.aae import run_aae_check
    background_tasks.add_task(run_aae_check)
    return {"status": "success", "message": "AAE check queued"}


@router.get("/aae/logs")
def get_aae_logs(limit: int = 50):
    """Return the most recent notification dispatch logs."""
    rows = run_query(
        """
        MATCH (s:Student)-[:HAS_NOTIFICATION]->(n:NotificationLog)
        RETURN s.name AS student, s.email AS email,
               n.channel AS channel, n.status AS status,
               n.riskScore AS score, n.sentAt AS sentAt
        ORDER BY n.sentAt DESC
        LIMIT $limit
        """,
        {"limit": limit},
    )
    return {"status": "success", "logs": rows, "count": len(rows)}


@router.get("/aae/at-risk")
def get_at_risk_preview():
    """Preview which students would be nudged right now (read-only, no send)."""
    from app.services.aae import find_at_risk_students
    students = find_at_risk_students()
    return {
        "status": "success",
        "count": len(students),
        "students": [
            {"name": s["name"], "email": s["email"],
             "score": s["score"], "risk_tier": s["risk_tier"]}
            for s in students
        ],
    }


@router.post("/parent-summaries/trigger")
def trigger_parent_summaries(background_tasks: BackgroundTasks):
    """Manually trigger the weekly parent summary run."""
    from app.services.parent_summary import run_parent_summaries
    background_tasks.add_task(run_parent_summaries)
    return {"status": "success", "message": "Parent summary run queued"}


@router.get("/stats")
def platform_stats():
    """High-level platform statistics."""
    rows = run_query(
        """
        MATCH (s:Student) WITH count(s) AS students
        MATCH (n:NotificationLog) WITH students, count(n) AS nudges
        MATCH (e:EngagementLog) WITH students, nudges, count(e) AS events
        RETURN students, nudges, events
        """
    )
    row = rows[0] if rows else {}
    node_count = run_query("MATCH (n) RETURN count(n) AS total")[0]["total"]
    return {
        "students": row.get("students", 0),
        "nudges_sent": row.get("nudges", 0),
        "engagement_events": row.get("events", 0),
        "total_nodes": node_count,
    }
