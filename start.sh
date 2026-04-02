#!/bin/bash
set -e

echo "=== Starting Koffe ==="
echo "PORT=${PORT:-8000}"
echo "PWD=$(pwd)"
echo "Contents of /app/koffe/frontend:"
ls -la /app/koffe/frontend/ 2>&1 || echo "  /app/koffe/frontend NOT FOUND"
echo "Contents of /app/data:"
ls -la /app/data/ 2>&1 || echo "  /app/data NOT FOUND"

# Ensure data/images exists (Render disk mount may be empty)
mkdir -p /app/data/images

echo "=== Testing import ==="
python -c "from koffe.api.main import app; print('Import OK')" 2>&1

echo "=== Starting uvicorn ==="
exec uvicorn koffe.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
