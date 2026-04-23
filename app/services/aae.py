"""
S-7 FR-08: Anti-Abandonment Engine.

Daily cron (default 09:00) — detects students who have gone silent for
AAE_INACTIVITY_DAYS days and whose engagement risk tier is 'low' or 'medium'.
Sends a personalised nudge via email (dry-run log if SMTP not configured).
Persists every dispatch attempt as a NotificationLog node in Neo4j.
"""

import uuid
import logging
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from google import genai

from app.core.config import settings
from app.core.database import run_query

logger = logging.getLogger(__name__)

_EVENT_WEIGHTS = {
    "login": 1,
    "chat_message": 3,
    "module_view": 2,
    "article_view": 1,
    "module_complete": 5,
}
_MAX_SCORE = 14 * (3 * 3 + 2 * 2 + 1)


# ── Risk detection ────────────────────────────────────────────────────────────

def _compute_score(student_id: str, cutoff: str) -> tuple[float, str]:
    rows = run_query(
        """
        MATCH (s:Student {id: $sid})-[:HAS_LOG]->(e:EngagementLog)
        WHERE e.timestamp >= $cutoff
        RETURN e.eventType AS event_type, count(e) AS cnt
        """,
        {"sid": student_id, "cutoff": cutoff},
    )
    weighted = sum(_EVENT_WEIGHTS.get(r["event_type"], 1) * r["cnt"] for r in rows)
    score = min(100.0, round(weighted / _MAX_SCORE * 100, 1)) if _MAX_SCORE else 0.0
    tier = "high" if score >= 60 else "medium" if score >= 30 else "low"
    return score, tier


def find_at_risk_students() -> list[dict]:
    """
    Returns students who:
    - have had no engagement event in the last AAE_INACTIVITY_DAYS days, OR
    - have a low/medium engagement score over the last 14 days
    Excludes students who already received a nudge in the last 3 days.
    """
    cutoff_14 = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    cutoff_inactivity = (
        datetime.now(timezone.utc) - timedelta(days=settings.aae_inactivity_days)
    ).isoformat()
    nudge_cooldown = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()

    # Students who have not been nudged recently
    students = run_query(
        """
        MATCH (s:Student)
        WHERE NOT EXISTS {
            MATCH (s)-[:HAS_NOTIFICATION]->(n:NotificationLog)
            WHERE n.sentAt >= $cooldown
        }
        OPTIONAL MATCH (s)-[:TARGETS]->(c:Course)
        RETURN s.id AS id, s.name AS name, s.email AS email,
               s.careerGoal AS careerGoal, c.name AS targetCourse
        """,
        {"cooldown": nudge_cooldown},
    )

    at_risk = []
    for s in students:
        if not s.get("email"):
            continue
        score, tier = _compute_score(s["id"], cutoff_14)

        # Check last activity
        last_event = run_query(
            """
            MATCH (s:Student {id: $sid})-[:HAS_LOG]->(e:EngagementLog)
            RETURN e.timestamp AS ts ORDER BY e.timestamp DESC LIMIT 1
            """,
            {"sid": s["id"]},
        )
        last_ts = last_event[0]["ts"] if last_event else None
        inactive = last_ts is None or last_ts < cutoff_inactivity

        if tier in ("low", "medium") or inactive:
            at_risk.append({**s, "score": score, "risk_tier": tier, "inactive": inactive})

    logger.info(f"AAE: {len(at_risk)} at-risk students found out of {len(students)} total")
    return at_risk


# ── Nudge generation ──────────────────────────────────────────────────────────

def _generate_nudge(student: dict) -> str:
    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        prompt = (
            f"Write a short, warm, encouraging email (3–4 sentences) to a Sri Lankan university student "
            f"named {student['name']} who is studying {student.get('targetCourse') or 'IT'}. "
            f"Their career goal is: {student.get('careerGoal') or 'working in tech'}. "
            f"They have been inactive on LearNexus for a few days. "
            f"Remind them gently that their modules are waiting and one small step today makes a big difference. "
            f"Sign off as 'The LearNexus Team'. Plain text only, no markdown."
        )
        resp = client.models.generate_content(model=settings.gemini_model, contents=prompt)
        return resp.text.strip()
    except Exception as e:
        logger.warning(f"Gemini nudge generation failed: {e} — using fallback")
        course = student.get("targetCourse") or "your course"
        return (
            f"Hi {student['name']},\n\n"
            f"We noticed you haven't visited LearNexus in a while. "
            f"Your modules in {course} are still waiting for you! "
            f"Even 15 minutes today can keep your momentum going.\n\n"
            f"The LearNexus Team"
        )


# ── Email dispatch ────────────────────────────────────────────────────────────

def _send_email(to: str, subject: str, body: str) -> bool:
    if not settings.smtp_host:
        logger.info(f"[DRY-RUN] Would email {to}: {subject[:60]}")
        return True   # dry-run counts as "sent" for logging purposes

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = to
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.ehlo()
            server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_from, [to], msg.as_string())
        return True
    except Exception as e:
        logger.error(f"SMTP error sending to {to}: {e}")
        return False


# ── Neo4j persistence ─────────────────────────────────────────────────────────

def _log_notification(student_id: str, channel: str, message: str, status: str, score: float) -> None:
    run_query(
        """
        MATCH (s:Student {id: $sid})
        CREATE (n:NotificationLog {
            id: $lid,
            channel: $channel,
            message: $message,
            status: $status,
            riskScore: $score,
            sentAt: $ts
        })
        CREATE (s)-[:HAS_NOTIFICATION]->(n)
        """,
        {
            "sid": student_id,
            "lid": str(uuid.uuid4()),
            "channel": channel,
            "message": message[:500],
            "status": status,
            "score": score,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    )


# ── Main job ──────────────────────────────────────────────────────────────────

def run_aae_check() -> dict:
    logger.info("AAE: starting daily check")
    at_risk = find_at_risk_students()

    sent = 0
    failed = 0
    for student in at_risk:
        message = _generate_nudge(student)
        subject = f"Hey {student['name']}, your LearNexus journey continues 🚀"
        ok = _send_email(student["email"], subject, message)
        status = "sent" if ok else "failed"
        _log_notification(student["id"], "email", message, status, student["score"])
        if ok:
            sent += 1
        else:
            failed += 1
        logger.info(f"AAE: nudge {status} → {student['email']} (score={student['score']})")

    summary = {"checked": len(at_risk), "sent": sent, "failed": failed,
               "ran_at": datetime.now(timezone.utc).isoformat()}
    logger.info(f"AAE: complete — {summary}")
    return summary
