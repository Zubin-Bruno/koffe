"""Diagnostic app: tries to import the real app, serves error via /debug."""
import sys
import traceback
import io

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

app = FastAPI()

_import_error = None

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/")
def root():
    return {"message": "Koffe diagnostic", "import_ok": _import_error is None}

@app.get("/debug", response_class=PlainTextResponse)
def debug():
    if _import_error:
        return f"IMPORT FAILED:\n{_import_error}"
    return "All imports OK"

@app.on_event("startup")
async def startup():
    global _import_error
    try:
        # Step 1: test basic imports
        import pathlib
        import os
        info = []
        info.append(f"PWD: {os.getcwd()}")
        info.append(f"PYTHONPATH: {os.environ.get('PYTHONPATH', 'not set')}")
        info.append(f"PORT: {os.environ.get('PORT', 'not set')}")

        app_dir = pathlib.Path("/app")
        if app_dir.exists():
            info.append(f"/app contents: {list(app_dir.iterdir())}")

        koffe_dir = pathlib.Path("/app/koffe")
        if koffe_dir.exists():
            info.append(f"/app/koffe contents: {list(koffe_dir.iterdir())}")

        frontend_dir = pathlib.Path("/app/koffe/frontend")
        if frontend_dir.exists():
            info.append(f"/app/koffe/frontend contents: {list(frontend_dir.iterdir())}")
        else:
            info.append("/app/koffe/frontend DOES NOT EXIST")

        data_dir = pathlib.Path("/app/data")
        info.append(f"/app/data exists: {data_dir.exists()}")
        if data_dir.exists():
            info.append(f"/app/data contents: {list(data_dir.iterdir())}")

        # Step 2: try importing the real app
        from koffe.api.main import app as real_app
        info.append("koffe.api.main imported OK")

        _import_error = None
        print("\n".join(info), flush=True)

    except Exception as e:
        buf = io.StringIO()
        traceback.print_exc(file=buf)
        _import_error = buf.getvalue()
        print(f"IMPORT FAILED: {_import_error}", flush=True)
