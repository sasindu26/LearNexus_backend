"""
S-8 FR-09: Parent registration, consent flow, and summary preview.

Flow:
  1. Student shares their studentId with parent
  2. Parent registers: POST /parent/register  → consent token emailed
  3. Parent confirms: GET  /parent/consent/{token}
  4. Weekly cron sends summaries automatically
"""

import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.core.crypto import encrypt_phone
from app.core.database import run_query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/parent", tags=["parent"])


class ParentRegisterRequest(BaseModel):
    name: str
    email: str
    phone: Optional[str] = None
    student_id: str


class ParentRegisterResponse(BaseModel):
    status: str
    parentId: str
    message: str


@router.post("/register", response_model=ParentRegisterResponse, status_code=201)
def register_parent(body: ParentRegisterRequest):
    # Verify student exists
    student = run_query(
        "MATCH (s:Student {id: $id}) RETURN s.name AS name",
        {"id": body.student_id},
    )
    if not student:
        raise HTTPException(
            status_code=404,
            detail={"status": "error", "message": "Student not found"},
        )

    # Prevent duplicate parent registrations for the same student+email
    existing = run_query(
        "MATCH (p:Parent {email: $email})-[:PARENT_OF]->(s:Student {id: $sid}) RETURN p.id AS id",
        {"email": body.email, "sid": body.student_id},
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail={"status": "error", "message": "Already registered for this student"},
        )

    parent_id = str(uuid.uuid4())
    consent_token = str(uuid.uuid4())
    phone_encrypted = encrypt_phone(body.phone) if body.phone else ""

    run_query(
        """
        CREATE (p:Parent {
            id: $pid, name: $name, email: $email,
            phone: $phone, consentGiven: false,
            consentToken: $token, createdAt: $ts
        })
        WITH p
        MATCH (s:Student {id: $sid})
        CREATE (p)-[:PARENT_OF]->(s)
        """,
        {
            "pid": parent_id, "name": body.name, "email": body.email,
            "phone": phone_encrypted, "token": consent_token,
            "sid": body.student_id, "ts": datetime.now(timezone.utc).isoformat(),
        },
    )

    # In production send the token via email; here we return it so the
    # frontend can display a "check your email" page and also expose the link
    logger.info(f"Parent registered: {body.email} → student {body.student_id}")
    logger.info(f"Consent link: /parent/consent/{consent_token}")

    return ParentRegisterResponse(
        status="success",
        parentId=parent_id,
        message=(
            f"Registration received. Please confirm consent by visiting: "
            f"/parent/consent/{consent_token} "
            f"(In production this link is emailed to {body.email})"
        ),
    )


@router.get("/consent/{token}")
def confirm_consent(token: str):
    rows = run_query(
        "MATCH (p:Parent {consentToken: $token}) RETURN p.id AS id, p.name AS name",
        {"token": token},
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail={"status": "error", "message": "Invalid or expired consent token"},
        )

    run_query(
        """
        MATCH (p:Parent {consentToken: $token})
        SET p.consentGiven = true, p.consentAt = $ts, p.consentToken = null
        """,
        {"token": token, "ts": datetime.now(timezone.utc).isoformat()},
    )
    return {
        "status": "success",
        "message": f"Thank you, {rows[0]['name']}. You will now receive weekly summaries.",
    }


@router.get("/consent/revoke/{parent_id}")
def revoke_consent(parent_id: str):
    run_query(
        "MATCH (p:Parent {id: $pid}) SET p.consentGiven = false",
        {"pid": parent_id},
    )
    return {"status": "success", "message": "Consent revoked. No further summaries will be sent."}


@router.get("/summary/preview")
def preview_summary(current_user: dict = Depends(get_current_user)):
    """Student can preview the summary their parents will receive."""
    from app.services.parent_summary import _get_student_snapshot, _generate_summary
    snap = _get_student_snapshot(current_user["sub"])
    if not snap:
        raise HTTPException(status_code=404, detail={"status": "error", "message": "Profile not found"})

    text = _generate_summary("Parent", snap)
    return {"status": "success", "preview": text, "snapshot": snap}


@router.get("/list")
def list_parents(current_user: dict = Depends(get_current_user)):
    """Student sees who is registered as their parent."""
    rows = run_query(
        """
        MATCH (p:Parent)-[:PARENT_OF]->(s:Student {id: $sid})
        RETURN p.id AS id, p.name AS name, p.email AS email,
               p.consentGiven AS consent, p.createdAt AS registeredAt
        """,
        {"sid": current_user["sub"]},
    )
    return {"status": "success", "parents": rows}
