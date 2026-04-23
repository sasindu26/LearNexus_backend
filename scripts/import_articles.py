"""
S-4: Import dev.to articles into Neo4j as Article nodes.
Links each article to relevant Module nodes via RELATED_TO (tag + title keyword match).
Run once: python -m scripts.import_articles
"""

import sys, os, json, uuid, hashlib
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core.database import run_query

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

ARTICLE_FILES = [
    _DATA_DIR / "devto_articles2.json",
    _DATA_DIR / "devto_articles3.json",
    _DATA_DIR / "devto_articles_5.json",
    _DATA_DIR / "devto_articles_7.json",
]


def load_unique_articles() -> list[dict]:
    seen_urls = set()
    articles = []
    for path in ARTICLE_FILES:
        try:
            data = json.load(open(path, encoding="utf-8"))
            for a in data:
                url = a.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    articles.append(a)
        except Exception as e:
            print(f"  Warning: could not read {path}: {e}")
    return articles


def _stable_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def _find_related_modules(article: dict, all_modules: list[str]) -> list[str]:
    """Match article tags and title words against module names."""
    tags = [t.lower() for t in (article.get("tags") or [])]
    title_words = set(article.get("title", "").lower().split())
    related = []
    for module in all_modules:
        module_lower = module.lower()
        module_words = set(module_lower.split())
        if (
            any(tag in module_lower for tag in tags)
            or any(t in module_lower for t in tags)
            or len(module_words & title_words) >= 2
        ):
            related.append(module)
    return related[:5]


def import_articles(articles: list[dict], all_modules: list[str]) -> tuple[int, int]:
    created = 0
    linked = 0

    for article in articles:
        url = article.get("url", "")
        if not url:
            continue
        article_id = _stable_id(url)
        tags = article.get("tags") or []

        run_query(
            """
            MERGE (a:Article {id: $id})
            SET a.title = $title,
                a.description = $description,
                a.url = $url,
                a.tags = $tags,
                a.published_at = $published_at,
                a.full_description = $full_description
            """,
            {
                "id": article_id,
                "title": article.get("title", ""),
                "description": article.get("description", ""),
                "url": url,
                "tags": tags,
                "published_at": article.get("published_at", ""),
                "full_description": (article.get("full_description") or "")[:2000],
            },
        )
        created += 1

        related = _find_related_modules(article, all_modules)
        for module_name in related:
            run_query(
                """
                MATCH (a:Article {id: $aid}), (m:Module {name: $mname})
                MERGE (a)-[:RELATED_TO]->(m)
                """,
                {"aid": article_id, "mname": module_name},
            )
            linked += 1

    return created, linked


def main():
    print("Loading articles...")
    articles = load_unique_articles()
    print(f"  Found {len(articles)} unique articles")

    print("Loading module names from Neo4j...")
    rows = run_query("MATCH (m:Module) RETURN m.name AS name")
    all_modules = [r["name"] for r in rows if r.get("name")]
    print(f"  Found {len(all_modules)} modules")

    print("Importing into Neo4j...")
    created, linked = import_articles(articles, all_modules)
    print(f"\nDone. {created} articles created, {linked} RELATED_TO links made.")

    sample = run_query(
        "MATCH (a:Article)-[:RELATED_TO]->(m:Module) "
        "RETURN a.title AS title, m.name AS module LIMIT 5"
    )
    if sample:
        print("\nSample links:")
        for r in sample:
            print(f"  [{r['module']}] {r['title'][:60]}")


if __name__ == "__main__":
    main()
