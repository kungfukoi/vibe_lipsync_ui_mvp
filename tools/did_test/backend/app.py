import os
from pathlib import Path
from fastapi import FastAPI

# Load .env in THIS folder
ENV_PATH = Path(__file__).parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

app = FastAPI()

@app.get("/api/health")
def health():
    fal_ok = bool(os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY"))
    eleven_ok = bool(os.environ.get("ELEVEN_API_KEY"))
    return {
        "ok": True,
        "FAL_KEY_loaded": fal_ok,
        "ELEVEN_API_KEY_loaded": eleven_ok,
    }