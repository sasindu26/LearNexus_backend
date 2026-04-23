from fastapi import APIRouter, Depends
from typing import Optional
import numpy as np
from sentence_transformers import SentenceTransformer

from app.core.auth import get_current_user_optional
from app.core.database import run_query

router = APIRouter()

_encoder: Optional[SentenceTransformer] = None

def _get_encoder() -> SentenceTransformer:
    global _encoder
    if _encoder is None:
        _encoder = SentenceTransformer("all-MiniLM-L6-v2")
    return _encoder


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


@router.get("/recommendations")
def get_recommendations(current_user: Optional[dict] = Depends(get_current_user_optional)):
    """
    Personalised module recommendations ranked by cosine similarity to the
    student's career goal + interests. Excludes already-completed modules.
    Falls back to top modules by course if no profile is available.
    """
    enc = _get_encoder()
    profile_text = "software engineering programming"
    completed: set[str] = set()
    target_course: Optional[str] = None

    if current_user:
        rows = run_query(
            """
            MATCH (s:Student {id: $id})
            OPTIONAL MATCH (s)-[:TARGETS]->(c:Course)
            OPTIONAL MATCH (s)-[:COMPLETED]->(m:Module)
            RETURN s.careerGoal AS goal, s.interests AS interests,
                   c.name AS course, collect(m.name) AS completed
            """,
            {"id": current_user["sub"]},
        )
        if rows:
            r = rows[0]
            goal = r.get("goal") or ""
            interests = " ".join(r.get("interests") or [])
            profile_text = f"{goal} {interests}".strip() or profile_text
            completed = set(r.get("completed") or [])
            target_course = r.get("course")

    profile_vec = enc.encode(profile_text)

    # Fetch modules from the student's enrolled course first, then all others
    if target_course:
        rows = run_query(
            """
            MATCH (c:Course {name: $course})-[:CONTAINS]->(m:Module)
            WHERE m.embedding IS NOT NULL AND NOT m.name IN $done
            RETURN m.name AS name, m.embedding AS embedding
            """,
            {"course": target_course, "done": list(completed)},
        )
    else:
        rows = run_query(
            "MATCH (m:Module) WHERE m.embedding IS NOT NULL AND NOT m.name IN $done "
            "RETURN m.name AS name, m.embedding AS embedding",
            {"done": list(completed)},
        )

    scored = []
    for r in rows:
        if r.get("embedding"):
            emb = np.array(r["embedding"])
            score = _cosine(profile_vec, emb)
            scored.append({"name": r["name"], "score": round(score, 4)})

    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:8]

    return {
        "profile_text": profile_text,
        "target_course": target_course,
        "recommended_modules": [m["name"] for m in top],
        "scores": {m["name"]: m["score"] for m in top},
    }
