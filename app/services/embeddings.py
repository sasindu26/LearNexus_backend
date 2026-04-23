from sentence_transformers import SentenceTransformer
import numpy as np
from app.core.database import run_query

_model = SentenceTransformer("all-MiniLM-L6-v2")


def embed_text(text: str) -> list[float]:
    return _model.encode(text).tolist()


def update_module_embeddings():
    """Generate and store embeddings for all Module nodes that don't have one."""
    modules = run_query(
        "MATCH (m:Module) WHERE m.embedding IS NULL RETURN m.name AS name"
    )
    for row in modules:
        name = row["name"]
        embedding = embed_text(name)
        run_query(
            "MATCH (m:Module {name: $name}) SET m.embedding = $embedding",
            {"name": name, "embedding": embedding},
        )
    return len(modules)
