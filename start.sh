#!/bin/sh
echo "=== Koffe starting ==="
echo "PORT=${PORT:-not set}"
echo "PWD=$(pwd)"
ls -la /app/koffe/frontend/ || echo "frontend dir MISSING"
ls -la /app/data/ || echo "data dir MISSING"
mkdir -p /app/data/images
python -c "
import sys, traceback
try:
    from koffe.api.main import app
    print('Import OK', flush=True)
except Exception:
    traceback.print_exc()
    sys.exit(1)
"
if [ $? -ne 0 ]; then
    echo "IMPORT FAILED - exiting"
    exit 1
fi
echo "=== Starting uvicorn ==="
exec uvicorn koffe.api.main:app --host 0.0.0.0 --port ${PORT:-10000}
