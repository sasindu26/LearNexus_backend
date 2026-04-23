"""
S-4: Generate and store embeddings for all Module and Topic nodes in Neo4j.
Run once: python -m scripts.generate_embeddings
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sentence_transformers import SentenceTransformer
from app.core.database import run_query

_BATCH = 32


def embed_nodes(label: str, model: SentenceTransformer) -> int:
    rows = run_query(
        f"MATCH (n:{label}) WHERE n.embedding IS NULL "
        f"RETURN n.name AS name, coalesce(n.description, '') AS description"
    )
    if not rows:
        print(f"  {label}: all already embedded")
        return 0

    texts = [f"{r['name']}. {r['description']}".strip() for r in rows]
    names = [r["name"] for r in rows]

    print(f"  {label}: embedding {len(rows)} nodes in batches of {_BATCH}...")
    done = 0
    for i in range(0, len(texts), _BATCH):
        batch_texts = texts[i:i + _BATCH]
        batch_names = names[i:i + _BATCH]
        embeddings = model.encode(batch_texts, show_progress_bar=False).tolist()
        for name, emb in zip(batch_names, embeddings):
            run_query(
                f"MATCH (n:{label} {{name: $name}}) SET n.embedding = $embedding",
                {"name": name, "embedding": emb},
            )
        done += len(batch_texts)
        print(f"    {done}/{len(rows)} done", end="\r")
    print()
    return len(rows)


def main():
    print("Loading sentence-transformer model...")
    model = SentenceTransformer("all-MiniLM-L6-v2")

    total = 0
    for label in ("Module", "Topic"):
        total += embed_nodes(label, model)

    print(f"\nDone. {total} nodes embedded.")


if __name__ == "__main__":
    main()
