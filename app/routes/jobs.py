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


JOB_ROLES = [
    {
        "role": "Software Engineer",
        "description": "Design, develop and maintain software applications using programming languages, data structures, algorithms, object-oriented design, and software engineering principles.",
        "keywords": ["software", "programming", "object", "algorithm", "development"],
    },
    {
        "role": "Data Scientist",
        "description": "Analyse large datasets using machine learning, statistics, Python, and data visualisation to derive business insights and build predictive models.",
        "keywords": ["data", "machine learning", "statistics", "python", "analytics"],
    },
    {
        "role": "Network Engineer",
        "description": "Design and manage computer networks, configure routers and switches, implement network security protocols and ensure reliable infrastructure.",
        "keywords": ["network", "security", "infrastructure", "protocol", "routing"],
    },
    {
        "role": "Cybersecurity Analyst",
        "description": "Protect systems from cyber threats, conduct security audits, implement encryption, and respond to incidents using ethical hacking techniques.",
        "keywords": ["security", "cyber", "encryption", "ethical", "vulnerability"],
    },
    {
        "role": "Full Stack Developer",
        "description": "Build complete web applications handling both front-end interfaces and back-end server logic, databases, and APIs.",
        "keywords": ["web", "frontend", "backend", "database", "api"],
    },
    {
        "role": "Mobile App Developer",
        "description": "Create mobile applications for Android and iOS platforms using frameworks and mobile-first design principles.",
        "keywords": ["mobile", "android", "ios", "application", "ui"],
    },
    {
        "role": "Cloud Engineer",
        "description": "Deploy and manage cloud infrastructure, implement DevOps practices, containerisation with Docker and Kubernetes, and CI/CD pipelines.",
        "keywords": ["cloud", "devops", "infrastructure", "deployment", "container"],
    },
    {
        "role": "AI / ML Engineer",
        "description": "Build and deploy machine learning models, neural networks, deep learning systems, and natural language processing pipelines at scale.",
        "keywords": ["machine learning", "neural", "deep learning", "ai", "nlp"],
    },
    {
        "role": "Database Administrator",
        "description": "Design, optimise and maintain relational and NoSQL databases, write complex queries, ensure data integrity and performance tuning.",
        "keywords": ["database", "sql", "nosql", "query", "data management"],
    },
    {
        "role": "IT Project Manager",
        "description": "Lead software projects using agile and scrum methodologies, manage teams, plan sprints, and ensure on-time delivery.",
        "keywords": ["project management", "agile", "scrum", "planning", "leadership"],
    },
]


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


def _find_relevant_modules(keywords: list[str], all_modules: list[dict], top_k: int = 6) -> list[str]:
    enc = _get_encoder()
    query = " ".join(keywords)
    qvec = enc.encode(query)
    scored = []
    for m in all_modules:
        if m.get("embedding"):
            emb = np.array(m["embedding"])
            scored.append((m["name"], _cosine(qvec, emb)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [name for name, _ in scored[:top_k]]


@router.get("/job-roles")
def get_job_roles(current_user: Optional[dict] = Depends(get_current_user_optional)):
    enc = _get_encoder()

    # Build student profile text
    profile_text = "IT professional software technology"
    completed_module_names: set[str] = set()

    if current_user:
        rows = run_query(
            """
            MATCH (s:Student {id: $id})
            OPTIONAL MATCH (s)-[:COMPLETED]->(m:Module)
            RETURN s.careerGoal AS goal, s.interests AS interests,
                   collect(m.name) AS completed
            """,
            {"id": current_user["sub"]},
        )
        if rows:
            r = rows[0]
            goal = r.get("goal") or ""
            interests = " ".join(r.get("interests") or [])
            profile_text = f"{goal} {interests}".strip() or profile_text
            completed_module_names = set(r.get("completed") or [])

    profile_vec = enc.encode(profile_text)

    # Embed all job role descriptions
    role_descs = [j["description"] for j in JOB_ROLES]
    role_vecs = enc.encode(role_descs)

    # Score each role
    all_modules = run_query(
        "MATCH (m:Module) WHERE m.embedding IS NOT NULL "
        "RETURN m.name AS name, m.embedding AS embedding"
    )

    results = []
    for i, job in enumerate(JOB_ROLES):
        score = _cosine(profile_vec, role_vecs[i])
        required_modules = _find_relevant_modules(job["keywords"], all_modules)
        missing = [m for m in required_modules if m not in completed_module_names]
        done = [m for m in required_modules if m in completed_module_names]
        results.append({
            "role": job["role"],
            "match_score": round(score * 100, 1),
            "required_modules": required_modules,
            "completed_modules": done,
            "missing_modules": missing,
        })

    results.sort(key=lambda x: x["match_score"], reverse=True)
    return {"roles": results[:6]}
