import logging
from typing import Optional, List

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.core.database import run_query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/student", tags=["student"])


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    career_goal: Optional[str] = None
    interests: Optional[List[str]] = None


@router.get("/profile")
def get_profile(current_user: dict = Depends(get_current_user)):
    student_id = current_user["sub"]
    rows = run_query(
        """
        MATCH (s:Student {id: $id})
        OPTIONAL MATCH (s)-[:TARGETS]->(c:Course)
        RETURN s.id AS id, s.name AS name, s.email AS email,
               s.phone AS phone, s.careerGoal AS careerGoal,
               s.interests AS interests, s.alResults AS alResults,
               s.createdAt AS createdAt, c.name AS targetCourse
        """,
        {"id": student_id},
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": "error", "message": "Student not found"},
        )
    row = rows[0]
    return {
        "status": "success",
        "profile": {
            "id": row["id"],
            "name": row["name"],
            "email": row["email"],
            "phone": row.get("phone") or "",
            "careerGoal": row.get("careerGoal") or "",
            "interests": row.get("interests") or [],
            "alResults": row.get("alResults") or [],
            "createdAt": row.get("createdAt") or "",
            "targetCourse": row.get("targetCourse") or None,
        },
    }


@router.patch("/profile")
def update_profile(body: ProfileUpdate, current_user: dict = Depends(get_current_user)):
    student_id = current_user["sub"]

    updates = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.phone is not None:
        updates["phone"] = body.phone
    if body.career_goal is not None:
        updates["careerGoal"] = body.career_goal
    if body.interests is not None:
        updates["interests"] = body.interests

    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"status": "error", "message": "No fields to update"},
        )

    set_clause = ", ".join(f"s.{k} = ${k}" for k in updates)
    run_query(
        f"MATCH (s:Student {{id: $id}}) SET {set_clause}",
        {"id": student_id, **updates},
    )
    return {"status": "success", "message": "Profile updated"}


@router.get("/progress")
def get_progress(current_user: dict = Depends(get_current_user)):
    student_id = current_user["sub"]

    rows = run_query(
        """
        MATCH (s:Student {id: $id})-[:TARGETS]->(c:Course)
        OPTIONAL MATCH (c)-[:CONTAINS]->(m:Module)
        OPTIONAL MATCH (s)-[:COMPLETED]->(m)
        WITH c.name AS course,
             count(DISTINCT m) AS totalModules,
             count(DISTINCT CASE WHEN (s)-[:COMPLETED]->(m) THEN m END) AS completedModules
        RETURN course, totalModules, completedModules
        """,
        {"id": student_id},
    )

    if not rows:
        return {"status": "success", "progress": {"completedModules": 0, "totalModules": 0, "percentage": 0}}

    row = rows[0]
    total = row.get("totalModules") or 0
    completed = row.get("completedModules") or 0
    percentage = round((completed / total * 100), 1) if total > 0 else 0

    return {
        "status": "success",
        "progress": {
            "course": row.get("course"),
            "completedModules": completed,
            "totalModules": total,
            "percentage": percentage,
        },
    }


@router.post("/module/complete")
def complete_module(
    payload: dict,
    current_user: dict = Depends(get_current_user),
):
    student_id = current_user["sub"]
    module_name = payload.get("moduleName") or payload.get("module_name")
    if not module_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"status": "error", "message": "moduleName is required"},
        )

    rows = run_query(
        "MATCH (m:Module) WHERE toLower(m.name) = toLower($name) RETURN m.name AS name",
        {"name": module_name},
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": "error", "message": f"Module '{module_name}' not found"},
        )

    run_query(
        """
        MATCH (s:Student {id: $studentId}), (m:Module {name: $moduleName})
        MERGE (s)-[:COMPLETED]->(m)
        """,
        {"studentId": student_id, "moduleName": rows[0]["name"]},
    )
    return {"status": "success", "message": f"Module '{rows[0]['name']}' marked as completed"}
