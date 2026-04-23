#!/bin/sh
set -e

echo "==> Waiting for Neo4j to be ready..."
TRIES=0
until python -c "from app.core.database import run_query; run_query('RETURN 1')" 2>/dev/null; do
  TRIES=$((TRIES + 1))
  if [ "$TRIES" -ge 30 ]; then
    echo "ERROR: Neo4j did not become ready after 90 seconds." >&2
    exit 1
  fi
  sleep 3
done
echo "==> Neo4j is ready."

# Seed articles if none exist yet
ARTICLE_COUNT=$(python -c "from app.core.database import run_query; print(run_query('MATCH (a:Article) RETURN count(a) AS c')[0]['c'])")
if [ "$ARTICLE_COUNT" = "0" ]; then
  echo "==> Seeding articles..."
  python scripts/import_articles.py
fi

# Generate embeddings if missing
EMB_COUNT=$(python -c "from app.core.database import run_query; print(run_query('MATCH (n) WHERE n.embedding IS NOT NULL RETURN count(n) AS c')[0]['c'])")
if [ "$EMB_COUNT" = "0" ]; then
  echo "==> Generating embeddings (takes ~2 min on first run)..."
  python scripts/generate_embeddings.py
fi

echo "==> Starting LearNexus backend..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
