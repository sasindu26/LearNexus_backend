"""
S-8 FR-09: Parental summary service.

Weekly cron (default Monday 08:00) — generates a WhatsApp / email summary
for every parent who has given consent, summarising their child's progress.

Channels (in priority order):
  1. WhatsApp Business Cloud API  — when WHATSAPP_TOKEN + WHATSAPP_PHONE_NUMBER_ID set
  2. Email (SMTP)                 — when SMTP_HOST set
  3. Dry-run log                  — always available as fallback
"""

import uuid
import logging
import smtplib
import httpx
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from google import genai

from app.core.config import settings
from app.core.crypto import decrypt_phone
from app.core.database import run_query

logger = logging.getLogger(__name__)


# ── Data gathering ────────────────────────────────────────────────────────────

def _get_student_snapshot(student_id: str) -> dict:
    """Pull everything needed for a summary from Neo4j."""
    rows = run_query(
        """
        MATCH (s:Student {id: $id})
        OPTIONAL MATCH (s)-[:TARGETS]->(c:Course)
        OPTIONAL MATCH (s)-[:COMPLETED]->(m:Module)
        WITH s, c, collect(m.name) AS completed
        OPTIONAL MATCH (c)-[:CONTAINS]->(all_m:Module)
        RETURN s.name AS name, s.careerGoal AS goal,
               c.name AS course,
               count(DISTINCT all_m) AS totalModules,
               size(completed) AS completedModules,
               completed AS completedList
        """,
        {"id": student_id},
    )
    if not rows:
        return {}
    r = rows[0]

    # engagement score (last 14 days)
    from app.routes.engagement import EVENT_WEIGHTS, MAX_SCORE
    cutoff = (datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )).isoformat()
    eng_rows = run_query(
        """
        MATCH (s:Student {id: $id})-[:HAS_LOG]->(e:EngagementLog)
        WHERE e.timestamp >= $cutoff
        RETURN e.eventType AS et, count(e) AS cnt
        """,
        {"id": student_id, "cutoff": cutoff[:10]},   # just use date prefix
    )
    weighted = sum(EVENT_WEIGHTS.get(x["et"], 1) * x["cnt"] for x in eng_rows)
    score = min(100, round(weighted / MAX_SCORE * 100, 1)) if MAX_SCORE else 0

    return {
        "name": r["name"],
        "goal": r.get("goal") or "not set",
        "course": r.get("course") or "not enrolled",
        "completed": r.get("completedModules") or 0,
        "total": r.get("totalModules") or 0,
        "pct": round((r.get("completedModules") or 0) / max(r.get("totalModules") or 1, 1) * 100, 1),
        "recent": (r.get("completedList") or [])[-3:],
        "engagement_score": score,
    }


# ── Content generation ────────────────────────────────────────────────────────

def _generate_summary(parent_name: str, snap: dict) -> str:
    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        prompt = (
            f"Write a warm, concise weekly progress update (5–7 sentences) for a parent "
            f"named {parent_name} about their child {snap['name']} who is studying "
            f"{snap['course']} with a goal of becoming a {snap['goal']}. "
            f"This week: completed {snap['completed']} of {snap['total']} modules "
            f"({snap['pct']}% overall). Engagement score this week: {snap['engagement_score']}/100. "
            f"Recently completed modules: {', '.join(snap['recent']) or 'none yet'}. "
            f"Be encouraging, mention one specific thing to support at home, "
            f"and sign off as 'The LearNexus Team'. Plain text only."
        )
        resp = client.models.generate_content(model=settings.gemini_model, contents=prompt)
        return resp.text.strip()
    except Exception as e:
        logger.warning(f"Gemini summary generation failed: {e} — using template")
        return (
            f"Dear {parent_name},\n\n"
            f"Here is this week's update for {snap['name']}.\n"
            f"Course: {snap['course']}\n"
            f"Progress: {snap['completed']}/{snap['total']} modules ({snap['pct']}%)\n"
            f"Engagement score: {snap['engagement_score']}/100\n\n"
            f"Keep encouraging {snap['name']} to log in daily — consistency is the key to success.\n\n"
            f"The LearNexus Team"
        )


# ── Channel dispatch ──────────────────────────────────────────────────────────

def _send_whatsapp(phone_encrypted: str, message: str) -> bool:
    if not settings.whatsapp_token or not settings.whatsapp_phone_number_id:
        return False
    phone = decrypt_phone(phone_encrypted)
    # Ensure E.164 format (e.g. +94771234567)
    if not phone.startswith("+"):
        phone = "+94" + phone.lstrip("0")
    url = f"https://graph.facebook.com/v19.0/{settings.whatsapp_phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message},
    }
    try:
        resp = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info(f"WhatsApp sent to {phone[:7]}***")
        return True
    except Exception as e:
        logger.error(f"WhatsApp dispatch failed: {e}")
        return False


def _send_email_summary(to: str, parent_name: str, student_name: str, message: str) -> bool:
    if not settings.smtp_host:
        logger.info(f"[DRY-RUN] Would email parent {to}: weekly summary for {student_name}")
        return True
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"LearNexus — Weekly update for {student_name}"
        msg["From"] = settings.smtp_from
        msg["To"] = to
        msg.attach(MIMEText(message, "plain"))
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as s:
            s.ehlo(); s.starttls()
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.sendmail(settings.smtp_from, [to], msg.as_string())
        return True
    except Exception as e:
        logger.error(f"Email summary failed to {to}: {e}")
        return False


def _dispatch(parent: dict, message: str) -> tuple[str, bool]:
    """Try WhatsApp first, fall back to email, then dry-run."""
    if settings.whatsapp_token and parent.get("phone"):
        ok = _send_whatsapp(parent["phone"], message)
        if ok:
            return "whatsapp", True

    ok = _send_email_summary(
        parent["email"], parent["name"],
        parent.get("student_name", "your child"), message
    )
    return "email", ok


# ── Neo4j persistence ─────────────────────────────────────────────────────────

def _log_summary(parent_id: str, channel: str, content: str, status: str) -> None:
    run_query(
        """
        MATCH (p:Parent {id: $pid})
        CREATE (w:WeeklySummary {
            id: $sid, channel: $channel,
            content: $content, status: $status,
            sentAt: $ts
        })
        CREATE (p)-[:HAS_SUMMARY]->(w)
        """,
        {
            "pid": parent_id,
            "sid": str(uuid.uuid4()),
            "channel": channel,
            "content": content[:1000],
            "status": status,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    )


# ── Main job ──────────────────────────────────────────────────────────────────

def run_parent_summaries() -> dict:
    logger.info("Parent summaries: starting weekly run")

    parents = run_query(
        """
        MATCH (p:Parent {consentGiven: true})-[:PARENT_OF]->(s:Student)
        RETURN p.id AS pid, p.name AS pname, p.email AS pemail,
               p.phone AS pphone, s.id AS sid, s.name AS sname
        """
    )

    sent = failed = 0
    for p in parents:
        snap = _get_student_snapshot(p["sid"])
        if not snap:
            continue
        snap["student_name"] = p["sname"]

        parent_record = {
            "id": p["pid"], "name": p["pname"],
            "email": p["pemail"], "phone": p.get("pphone", ""),
            "student_name": p["sname"],
        }
        message = _generate_summary(p["pname"], snap)
        channel, ok = _dispatch(parent_record, message)
        status = "sent" if ok else "failed"
        _log_summary(p["pid"], channel, message, status)

        if ok:
            sent += 1
            logger.info(f"Summary {status} via {channel} → {p['pemail']}")
        else:
            failed += 1

    summary = {"parents_processed": len(parents), "sent": sent, "failed": failed,
               "ran_at": datetime.now(timezone.utc).isoformat()}
    logger.info(f"Parent summaries complete — {summary}")
    return summary
