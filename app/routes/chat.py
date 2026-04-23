import uuid
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app.core.auth import get_current_user_optional
from app.core.database import run_query

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    status: str
    message: str
    sources: list[str] = []
    session_id: str


@router.get("/health")
def health():
    try:
        run_query("RETURN 1")
        db_status = "connected"
    except Exception:
        db_status = "disconnected"
    return {"status": "ok", "database": db_status, "chatbot": "LearNexus AI Advisor"}


@router.get("/test-connection")
def test_connection():
    try:
        result = run_query("MATCH (n) RETURN count(n) AS total")
        return {"status": "connected", "node_count": result[0]["total"]}
    except Exception as e:
        raise HTTPException(status_code=503, detail={"status": "error", "message": str(e)})


def _load_student_profile(student_id: str) -> Optional[dict]:
    rows = run_query(
        """
        MATCH (s:Student {id: $id})
        OPTIONAL MATCH (s)-[:TARGETS]->(c:Course)
        RETURN s.name AS name, s.careerGoal AS careerGoal,
               s.interests AS interests, c.name AS targetCourse
        """,
        {"id": student_id},
    )
    return rows[0] if rows else None


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    if not request.message.strip():
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": "Empty message received"},
        )

    session_id = request.session_id or str(uuid.uuid4())

    student_profile = None
    if current_user:
        student_profile = _load_student_profile(current_user["sub"])

    try:
        from app.services.chatbot import get_chat_response
        reply, sources = await get_chat_response(
            message=request.message.strip(),
            session_id=session_id,
            student_profile=student_profile,
        )
        return ChatResponse(
            status="success",
            message=reply,
            sources=sources,
            session_id=session_id,
        )
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "message": "Chatbot unavailable. Check GEMINI_API_KEY."},
        )
