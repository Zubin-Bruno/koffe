"""Diagnostic entrypoint for Railway deployment.

Prints useful info to logs BEFORE importing the heavy app code,
so if something crashes during import we can see exactly what failed.
"""

import os
import sys
from pathlib import Path

port = os.environ.get("PORT", "8080")

print(f"[start] PORT={port}", flush=True)
print(f"[start] cwd={os.getcwd()}", flush=True)
print(f"[start] Python {sys.version}", flush=True)

# Ensure data directories exist and are writable
data_dir = Path("data/images")
data_dir.mkdir(parents=True, exist_ok=True)
print(f"[start] data/images/ exists={data_dir.exists()}", flush=True)

# Try importing the app — if this fails, the error will be visible in logs
try:
    from koffe.api.main import app  # noqa: F401

    print("[start] App imported OK", flush=True)
except Exception as e:
    print(f"[start] IMPORT FAILED: {e}", flush=True)
    sys.exit(1)

# Start uvicorn programmatically (no shell variable expansion needed)
import uvicorn

print(f"[start] Starting uvicorn on 0.0.0.0:{port}", flush=True)
uvicorn.run(app, host="0.0.0.0", port=int(port))
