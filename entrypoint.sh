#!/bin/sh

echo "==> Checking Neo4j connectivity..."
TRIES=0
NEO4J_READY=0
until python -c "from app.core.database import run_query; run_query('RETURN 1')" 2>/dev/null; do
  TRIES=$((TRIES + 1))
  if [ "$TRIES" -ge 10 ]; then
    echo "WARNING: Neo4j not reachable after 30 seconds, starting anyway."
    break
  fi
  sleep 3
done

if [ "$TRIES" -lt 10 ]; then
  echo "==> Neo4j is ready."

  # Run seeding in background so uvicorn starts immediately
  (
    ARTICLE_COUNT=$(python -c "from app.core.database import run_query; print(run_query('MATCH (a:Article) RETURN count(a) AS c')[0]['c'])" 2>/dev/null || echo "0")
    if [ "$ARTICLE_COUNT" = "0" ]; then
      echo "==> Seeding articles..."
      python scripts/import_articles.py || echo "WARNING: article seeding failed"
    fi

    EMB_COUNT=$(python -c "from app.core.database import run_query; print(run_query('MATCH (n) WHERE n.embedding IS NOT NULL RETURN count(n) AS c')[0]['c'])" 2>/dev/null || echo "0")
    if [ "$EMB_COUNT" = "0" ]; then
      echo "==> Generating embeddings..."
      python scripts/generate_embeddings.py || echo "WARNING: embedding generation failed"
    fi

    echo "==> Background seeding complete."
  ) &
fi

echo "==> Starting LearNexus backend..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
