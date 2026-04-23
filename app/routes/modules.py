from fastapi import APIRouter
from app.core.database import run_query
from app.models.schemas import ModuleResponse

router = APIRouter()


@router.get("/modules", response_model=list[ModuleResponse])
def get_modules():
    results = run_query(
        "MATCH (m:Module) OPTIONAL MATCH (c:Course)-[:CONTAINS]->(m) "
        "RETURN m.name AS name, c.name AS course, m.year AS year"
    )
    return [ModuleResponse(**r) for r in results]


