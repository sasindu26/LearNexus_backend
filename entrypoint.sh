#!/bin/sh
echo "==> Starting LearNexus backend on port 8000..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
