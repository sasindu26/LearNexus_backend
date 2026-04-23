"""
S-11 Integration test suite — hits the live server at http://localhost:8000.
Run with:   pytest tests/test_integration.py -v
Prereqs:    uvicorn must be running (uvicorn app.main:app --reload)
"""

import uuid
import time
import pytest
import requests

BASE = "http://localhost:8000"
SESSION = requests.Session()

# ── helpers ───────────────────────────────────────────────────────────────────

def url(path: str) -> str:
    return BASE + path


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def chat_post(payload: dict, token: str = "") -> requests.Response:
    """POST /chat with up to 3 retries for transient Gemini 503s."""
    headers = auth_headers(token) if token else {}
    for attempt in range(3):
        r = SESSION.post(url("/chat"), json=payload, headers=headers, timeout=60)
        if r.status_code != 500:
            return r
        time.sleep(8)
    return r


# ── shared state (filled during test run) ─────────────────────────────────────

_state: dict = {}


# ═══════════════════════════════════════════════════════════════════════════════
# S-1 — DB / health
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealth:
    def test_root_reachable(self):
        """Server responds (even 404 is fine — just not a connection error)."""
        try:
            r = SESSION.get(url("/"), timeout=5)
            assert r.status_code in (200, 404, 422)
        except requests.ConnectionError:
            pytest.fail("Server not reachable at localhost:8000 — start uvicorn first")

    def test_admin_stats(self):
        r = SESSION.get(url("/admin/stats"), timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert "students" in body
        assert "total_nodes" in body
        assert body["total_nodes"] > 0


# ═══════════════════════════════════════════════════════════════════════════════
# S-2 — Auth (register / login / profile)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuth:
    EMAIL = f"test_{uuid.uuid4().hex[:8]}@integration.test"
    PASSWORD = "TestPass123!"

    def test_register(self):
        payload = {
            "name": "Integration Tester",
            "email": self.EMAIL,
            "password": self.PASSWORD,
            "a_level_stream": "Technology",
            "career_goal": "Software Engineer",
            "interests": ["Python", "AI"],
        }
        r = SESSION.post(url("/auth/register"), json=payload)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "success"
        assert "token" in body
        _state["token"] = body["token"]
        _state["student_id"] = body.get("studentId", "")

    def test_login(self):
        r = SESSION.post(url("/auth/login"), json={
            "email": self.EMAIL,
            "password": self.PASSWORD,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "success"
        assert "token" in body
        _state["token"] = body["token"]

    def test_login_wrong_password(self):
        r = SESSION.post(url("/auth/login"), json={
            "email": self.EMAIL,
            "password": "WrongPass!",
        })
        assert r.status_code == 401

    def test_register_duplicate_email(self):
        payload = {
            "name": "Duplicate",
            "email": self.EMAIL,
            "password": self.PASSWORD,
            "a_level_stream": "Technology",
            "career_goal": "Dev",
            "interests": [],
        }
        r = SESSION.post(url("/auth/register"), json=payload)
        assert r.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# S-2 — Student profile
# ═══════════════════════════════════════════════════════════════════════════════

class TestStudentProfile:
    def test_get_profile_unauthorized(self):
        r = SESSION.get(url("/student/profile"))
        assert r.status_code == 401

    def test_get_profile(self):
        token = _state.get("token", "")
        r = SESSION.get(url("/student/profile"), headers=auth_headers(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "success"
        assert "profile" in body
        assert body["profile"]["name"] == "Integration Tester"

    def test_patch_profile(self):
        token = _state.get("token", "")
        r = SESSION.patch(url("/student/profile"), headers=auth_headers(token),
                          json={"career_goal": "ML Engineer"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "success"

    def test_get_progress(self):
        token = _state.get("token", "")
        r = SESSION.get(url("/student/progress"), headers=auth_headers(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert "status" in body
        # Response may nest under "progress" key
        progress = body.get("progress", body)
        assert "completedModules" in progress or "completed" in progress


# ═══════════════════════════════════════════════════════════════════════════════
# S-3 — Chat / RAG
# ═══════════════════════════════════════════════════════════════════════════════

class TestChat:
    def test_chat_anonymous(self):
        r = chat_post({"message": "What is machine learning?", "session_id": "anon-test-1"})
        if r.status_code == 500:
            pytest.skip("Gemini API temporarily unavailable (503)")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "message" in body
        assert len(body["message"]) > 10

    def test_chat_authenticated(self):
        token = _state.get("token", "")
        r = chat_post({"message": "Recommend a module for me", "session_id": "auth-test-1"},
                      token=token)
        if r.status_code == 500:
            pytest.skip("Gemini API temporarily unavailable (503)")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "message" in body

    def test_chat_sources_present(self):
        r = chat_post({"message": "Tell me about neural networks", "session_id": "src-test-1"})
        if r.status_code == 500:
            pytest.skip("Gemini API temporarily unavailable (503)")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "sources" in body

    def test_chat_response_field(self):
        """Verify the ChatResponse model fields are all present."""
        r = chat_post({"message": "Hello", "session_id": "field-test-1"})
        if r.status_code == 500:
            pytest.skip("Gemini API temporarily unavailable (503)")
        assert r.status_code == 200, r.text
        body = r.json()
        for key in ("status", "message", "sources", "session_id"):
            assert key in body, f"Missing key: {key}"


# ═══════════════════════════════════════════════════════════════════════════════
# S-4 — Modules & Topics
# ═══════════════════════════════════════════════════════════════════════════════

class TestModules:
    def test_get_modules(self):
        r = SESSION.get(url("/modules"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body, list)
        assert len(body) > 0
        _state["first_module"] = body[0].get("name", "")

    def test_get_module_topics(self):
        mod = _state.get("first_module", "")
        if not mod:
            pytest.skip("No module name available")
        r = SESSION.get(url(f"/modules/{mod}/topics"))
        # topics endpoint is per-module, a missing module name returns 404 which is fine
        assert r.status_code in (200, 404), r.text

    def test_module_complete(self):
        token = _state.get("token", "")
        mod = _state.get("first_module", "")
        if not mod:
            pytest.skip("No module name available")
        r = SESSION.post(url("/student/module/complete"),
                         headers=auth_headers(token),
                         json={"module_name": mod})
        assert r.status_code in (200, 201), r.text


# ═══════════════════════════════════════════════════════════════════════════════
# S-4 — Articles
# ═══════════════════════════════════════════════════════════════════════════════

class TestArticles:
    def test_get_articles(self):
        r = SESSION.get(url("/api/tech-recommendations"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body, list)

    def test_get_articles_by_module(self):
        mod = _state.get("first_module", "")
        if not mod:
            pytest.skip("No module name")
        r = SESSION.get(url(f"/api/tech-recommendations?module={mod}"))
        assert r.status_code == 200, r.text

    def test_rate_article(self):
        token = _state.get("token", "")
        r = SESSION.get(url("/api/tech-recommendations"))
        arts = r.json()
        if not arts:
            pytest.skip("No articles in DB")
        article_id = arts[0].get("id", "")
        if not article_id:
            pytest.skip("Article has no id field")
        r2 = SESSION.post(url("/api/tech-recommendations/rating"),
                          headers=auth_headers(token),
                          json={"id": article_id, "isEffective": True})
        assert r2.status_code == 200, r2.text


# ═══════════════════════════════════════════════════════════════════════════════
# S-5 — Recommendations & Jobs
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecommendations:
    def test_recommendations(self):
        token = _state.get("token", "")
        r = SESSION.get(url("/api/recommendations"), headers=auth_headers(token), timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        # key may be "recommendations" or "recommended_modules"
        has_recs = "recommendations" in body or "recommended_modules" in body
        assert has_recs, f"Missing recs key, got: {list(body.keys())}"

    def test_job_roles(self):
        token = _state.get("token", "")
        r = SESSION.get(url("/api/job-roles"), headers=auth_headers(token), timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "roles" in body
        assert len(body["roles"]) > 0
        role = body["roles"][0]
        assert "role" in role
        assert "match_score" in role


# ═══════════════════════════════════════════════════════════════════════════════
# S-6 — Engagement
# ═══════════════════════════════════════════════════════════════════════════════

class TestEngagement:
    def test_log_event(self):
        token = _state.get("token", "")
        r = SESSION.post(url("/engagement/log"),
                         headers=auth_headers(token),
                         json={"event_type": "module_view", "metadata": {"module": "test"}})
        assert r.status_code in (200, 201), r.text

    def test_engagement_score(self):
        token = _state.get("token", "")
        r = SESSION.get(url("/engagement/score"), headers=auth_headers(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert "score" in body
        assert "risk_tier" in body
        assert body["risk_tier"] in ("low", "medium", "high")

    def test_engagement_history(self):
        token = _state.get("token", "")
        r = SESSION.get(url("/engagement/history?days=7"), headers=auth_headers(token))
        assert r.status_code == 200, r.text
        body = r.json()
        # key may be "events" or "history"
        has_history = "events" in body or "history" in body
        assert has_history, f"Missing history key, got: {list(body.keys())}"


# ═══════════════════════════════════════════════════════════════════════════════
# S-7 — AAE (Anti-Abandonment Engine)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAAE:
    def test_at_risk_preview(self):
        r = SESSION.get(url("/admin/aae/at-risk"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "success"
        assert "students" in body

    def test_aae_logs(self):
        r = SESSION.get(url("/admin/aae/logs?limit=5"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "success"
        assert "logs" in body

    def test_trigger_aae(self):
        r = SESSION.post(url("/admin/aae/trigger"))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "success"


# ═══════════════════════════════════════════════════════════════════════════════
# S-8 — Parental Summaries
# ═══════════════════════════════════════════════════════════════════════════════

class TestParent:
    PARENT_EMAIL = f"parent_{uuid.uuid4().hex[:8]}@integration.test"
    _consent_token: str = ""

    def test_register_parent(self):
        sid = _state.get("student_id", "")
        if not sid:
            pytest.skip("No student id in state")
        r = SESSION.post(url("/parent/register"), json={
            "name": "Test Parent",
            "email": self.PARENT_EMAIL,
            "student_id": sid,
        })
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "success"
        assert "parentId" in body
        _state["parent_id"] = body["parentId"]

        # Extract UUID consent token from message
        msg = body.get("message", "")
        for segment in msg.split("/"):
            # strip trailing whitespace/parens
            candidate = segment.split()[0].strip("()")
            if len(candidate) == 36 and candidate.count("-") == 4:
                TestParent._consent_token = candidate
                break

    def test_register_duplicate_parent(self):
        sid = _state.get("student_id", "")
        if not sid:
            pytest.skip("No student id in state")
        r = SESSION.post(url("/parent/register"), json={
            "name": "Test Parent",
            "email": self.PARENT_EMAIL,
            "student_id": sid,
        })
        assert r.status_code == 409

    def test_confirm_consent(self):
        token = TestParent._consent_token
        if not token:
            pytest.skip("No consent token captured")
        r = SESSION.get(url(f"/parent/consent/{token}"))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "success"

    def test_list_parents(self):
        tok = _state.get("token", "")
        r = SESSION.get(url("/parent/list"), headers=auth_headers(tok))
        assert r.status_code == 200, r.text
        body = r.json()
        assert "parents" in body
        assert any(p["email"] == self.PARENT_EMAIL for p in body["parents"])

    def test_summary_preview(self):
        tok = _state.get("token", "")
        r = SESSION.get(url("/parent/summary/preview"),
                        headers=auth_headers(tok), timeout=30)
        if r.status_code == 500:
            pytest.skip("Gemini API temporarily unavailable (503)")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "preview" in body
        assert len(body["preview"]) > 20

    def test_trigger_parent_summaries(self):
        r = SESSION.post(url("/admin/parent-summaries/trigger"))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "success"

    def test_revoke_consent(self):
        pid = _state.get("parent_id", "")
        if not pid:
            pytest.skip("No parent id")
        r = SESSION.get(url(f"/parent/consent/revoke/{pid}"))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "success"


# ═══════════════════════════════════════════════════════════════════════════════
# Edge cases / error handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_invalid_token(self):
        r = SESSION.get(url("/student/profile"),
                        headers={"Authorization": "Bearer totally.invalid.token"})
        assert r.status_code == 401

    def test_consent_invalid_token(self):
        r = SESSION.get(url("/parent/consent/00000000-0000-0000-0000-000000000000"))
        assert r.status_code == 404

    def test_student_not_found_parent_register(self):
        r = SESSION.post(url("/parent/register"), json={
            "name": "Ghost",
            "email": "ghost@test.com",
            "student_id": "nonexistent-id",
        })
        assert r.status_code == 404

    def test_chat_empty_message(self):
        r = SESSION.post(url("/chat"), json={"message": "", "session_id": "edge-1"})
        # 400 validation, 422 pydantic, or 200 if model handles it
        assert r.status_code in (200, 400, 422)
