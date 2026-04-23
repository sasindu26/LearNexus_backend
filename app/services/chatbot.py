"""
Chatbot service — RAG pipeline over the LearNexus knowledge graph.

Flow:
  1. Encode the student's message with a sentence-transformer (Layer 1)
  2. Cosine-search Module + Topic embeddings in Neo4j (Layer 2 — RAG)
     → fall back to CONTAINS keyword search when embeddings are absent
  3. Assemble a grounded prompt with KG context (Layer 3)
  4. Prepend last N session messages for multi-turn memory (Layer 4)
  5. Optionally personalise with the student's profile (Layer 5)
  6. Call Gemini and return the reply
"""

import logging
import time
from typing import Optional

import numpy as np
from google import genai
from sentence_transformers import SentenceTransformer

from app.core.config import settings
from app.core.database import run_query

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=settings.gemini_api_key)
_encoder = SentenceTransformer("all-MiniLM-L6-v2")

_SYSTEM_PROMPT = (
    "You are LearNexus, an AI academic advisor helping Sri Lankan students who have "
    "completed their Advanced Level (A/L) exams choose the right IT degree and understand "
    "their course modules. Be concise, encouraging, and specific to the Sri Lankan university "
    "context. When you mention modules or topics, explain why they matter for the student's goal."
)

_HISTORY_LIMIT = 6   # message pairs kept in context window
_TOP_K = 5           # KG nodes returned per search


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


# ── Layer 2: KG retrieval ────────────────────────────────────────────────────

def _embedding_search(query_vec: np.ndarray, top_k: int) -> list[dict]:
    """Score Module and Topic nodes that already have stored embeddings."""
    rows = run_query(
        """
        MATCH (n) WHERE (n:Module OR n:Topic) AND n.embedding IS NOT NULL
        RETURN labels(n)[0] AS type, n.name AS name,
               coalesce(n.description, '') AS description, n.embedding AS embedding
        """
    )
    scored = []
    for r in rows:
        emb = np.array(r["embedding"])
        score = _cosine(query_vec, emb)
        scored.append({"type": r["type"], "name": r["name"],
                       "description": r["description"], "score": score})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def _keyword_fallback(query: str, top_k: int) -> list[dict]:
    """Text-match fallback when no embeddings exist in the graph."""
    words = [w for w in query.lower().split() if len(w) > 3]
    if not words:
        rows = run_query(
            "MATCH (m:Module) RETURN 'Module' AS type, m.name AS name, "
            "coalesce(m.description,'') AS description LIMIT $k",
            {"k": top_k},
        )
        return [{"type": r["type"], "name": r["name"],
                 "description": r["description"], "score": 0.0} for r in rows]

    conditions = " OR ".join(f"toLower(n.name) CONTAINS '{w}'" for w in words[:5])
    rows = run_query(
        f"""
        MATCH (n) WHERE (n:Module OR n:Topic) AND ({conditions})
        RETURN labels(n)[0] AS type, n.name AS name,
               coalesce(n.description, '') AS description
        LIMIT $k
        """,
        {"k": top_k},
    )
    return [{"type": r["type"], "name": r["name"],
             "description": r["description"], "score": 0.5} for r in rows]


def _find_relevant_context(query: str) -> list[dict]:
    """Return top-k relevant KG nodes for the query (embedding or keyword)."""
    query_vec = _encoder.encode(query)
    results = _embedding_search(query_vec, _TOP_K)
    if not results:
        logger.info("No embeddings in graph — using keyword fallback")
        results = _keyword_fallback(query, _TOP_K)
    return results


# ── Layer 4: session memory ──────────────────────────────────────────────────

def _load_history(session_id: str) -> list[dict]:
    """Return the last N message pairs from Neo4j for this session."""
    rows = run_query(
        """
        MATCH (s:Session {id: $sid})-[:HAS_MESSAGE]->(m:Message)
        RETURN m.role AS role, m.content AS content, m.timestamp AS ts
        ORDER BY m.timestamp DESC LIMIT $limit
        """,
        {"sid": session_id, "limit": _HISTORY_LIMIT * 2},
    )
    rows.reverse()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def _save_turn(session_id: str, user_msg: str, assistant_msg: str) -> None:
    """Persist a user+assistant exchange to Neo4j."""
    from datetime import datetime, timezone
    import uuid
    ts_user = datetime.now(timezone.utc).isoformat()
    ts_bot = datetime.now(timezone.utc).isoformat()
    run_query(
        """
        MERGE (s:Session {id: $sid})
        CREATE (u:Message {id: $uid, role: 'user', content: $umsg, timestamp: $uts})
        CREATE (a:Message {id: $aid, role: 'assistant', content: $amsg, timestamp: $ats})
        CREATE (s)-[:HAS_MESSAGE]->(u)
        CREATE (s)-[:HAS_MESSAGE]->(a)
        """,
        {
            "sid": session_id,
            "uid": str(uuid.uuid4()), "umsg": user_msg, "uts": ts_user,
            "aid": str(uuid.uuid4()), "amsg": assistant_msg, "ats": ts_bot,
        },
    )


# ── Layer 3 + 5: prompt assembly ─────────────────────────────────────────────

def _build_prompt(
    message: str,
    context_nodes: list[dict],
    history: list[dict],
    student_profile: Optional[dict] = None,
) -> str:
    parts = [_SYSTEM_PROMPT]

    if student_profile:
        parts.append(
            f"\nStudent profile — Name: {student_profile.get('name', 'Unknown')}, "
            f"Career goal: {student_profile.get('careerGoal') or 'not set'}, "
            f"Interests: {', '.join(student_profile.get('interests') or []) or 'not set'}, "
            f"Enrolled course: {student_profile.get('targetCourse') or 'not set'}."
        )

    if context_nodes:
        ctx_lines = [f"  - [{n['type']}] {n['name']}" +
                     (f": {n['description']}" if n["description"] else "")
                     for n in context_nodes]
        parts.append("\nRelevant content from the knowledge graph:\n" + "\n".join(ctx_lines))

    if history:
        conv = "\n".join(
            f"{'Student' if h['role'] == 'user' else 'LearNexus'}: {h['content']}"
            for h in history
        )
        parts.append(f"\nConversation so far:\n{conv}")

    parts.append(f"\nStudent: {message}\nLearNexus:")
    return "\n".join(parts)


# ── Public entry point ────────────────────────────────────────────────────────

async def get_chat_response(
    message: str,
    session_id: str,
    student_profile: Optional[dict] = None,
) -> tuple[str, list[str]]:
    context_nodes = _find_relevant_context(message)
    history = _load_history(session_id)
    prompt = _build_prompt(message, context_nodes, history, student_profile)

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            response = _client.models.generate_content(
                model=settings.gemini_model,
                contents=prompt,
            )
            reply = response.text.strip()
            break
        except Exception as e:
            last_exc = e
            if attempt < 2:
                wait = 5 * (attempt + 1)
                logger.warning(f"Gemini attempt {attempt + 1} failed ({e}), retrying in {wait}s")
                time.sleep(wait)
    else:
        logger.error(f"Gemini failed after 3 attempts: {last_exc}")
        raise last_exc

    _save_turn(session_id, message, reply)

    sources = [n["name"] for n in context_nodes]
    return reply, sources
