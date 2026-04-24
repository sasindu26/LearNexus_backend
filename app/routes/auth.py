import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List

import bcrypt
from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel

from app.core.auth import create_access_token, get_current_user
from app.core.config import settings
from app.core.database import run_query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


class ALResult(BaseModel):
    subject: str
    grade: str


class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str
    phone: Optional[str] = None
    al_stream: Optional[str] = None
    interests: Optional[List[str]] = []
    career_goal: Optional[str] = None
    al_results: Optional[List[ALResult]] = []


class LoginRequest(BaseModel):
    email: str
    password: str


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def _find_best_course(career_goal: str, interests: List[str]) -> Optional[str]:
    """Return the name of the most relevant Course node, or None."""
    courses = run_query("MATCH (c:Course) RETURN c.name AS name")
    if not courses:
        return None
    course_names = [c["name"] for c in courses if c.get("name")]
    if not course_names:
        return None

    goal_lower = (career_goal or "").lower()
    interest_lower = " ".join(interests).lower()
    combined = goal_lower + " " + interest_lower

    for name in course_names:
        if any(word in combined for word in name.lower().split()):
            return name
    return course_names[0]


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest):
    existing = run_query(
        "MATCH (s:Student {email: $email}) RETURN s.id AS id",
        {"email": body.email},
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"status": "error", "message": "Email already registered"},
        )

    student_id = str(uuid.uuid4())
    password_hash = _hash_password(body.password)
    created_at = datetime.now(timezone.utc).isoformat()
    al_results_str = [f"{r.subject}:{r.grade}" for r in (body.al_results or [])]

    run_query(
        """
        CREATE (s:Student {
            id: $id,
            name: $name,
            email: $email,
            passwordHash: $passwordHash,
            phone: $phone,
            alStream: $alStream,
            careerGoal: $careerGoal,
            interests: $interests,
            alResults: $alResults,
            createdAt: $createdAt
        })
        """,
        {
            "id": student_id,
            "name": body.name,
            "email": body.email,
            "passwordHash": password_hash,
            "phone": body.phone or "",
            "alStream": body.al_stream or "",
            "careerGoal": body.career_goal or "",
            "interests": body.interests or [],
            "alResults": al_results_str,
            "createdAt": created_at,
        },
    )

    course_name = _find_best_course(body.career_goal or "", body.interests or [])
    if course_name:
        run_query(
            """
            MATCH (s:Student {id: $studentId}), (c:Course {name: $courseName})
            MERGE (s)-[:TARGETS]->(c)
            """,
            {"studentId": student_id, "courseName": course_name},
        )

    token = create_access_token(student_id=student_id, email=body.email)
    return {"status": "success", "studentId": student_id, "token": token}


@router.post("/login")
def login(body: LoginRequest):
    rows = run_query(
        "MATCH (s:Student {email: $email}) RETURN s.id AS id, s.passwordHash AS passwordHash",
        {"email": body.email},
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "message": "Invalid email or password"},
        )

    row = rows[0]
    if not _verify_password(body.password, row["passwordHash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "message": "Invalid email or password"},
        )

    token = create_access_token(student_id=row["id"], email=body.email)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)).isoformat()
    return {"status": "success", "token": token, "expiresAt": expires_at}


@router.post("/logout")
def logout(current_user: dict = Depends(get_current_user)):
    # Token invalidation is client-side; server acknowledges the request
    return {"status": "success", "message": "Logged out successfully"}
