from pydantic import BaseModel
from typing import Optional, List


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    sources: Optional[List[str]] = []


class ModuleResponse(BaseModel):
    name: str
    course: Optional[str] = None
    year: Optional[int] = None
    topics: Optional[List[str]] = []


class RecommendationResponse(BaseModel):
    modules: List[str]
    resources: List[str]
    reasoning: Optional[str] = None


class JobRoleResponse(BaseModel):
    role: str
    match_score: Optional[float] = None
    required_modules: Optional[List[str]] = []
