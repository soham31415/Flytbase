"""FastAPI app: the web UI and JSON API. Run with exactly 1 worker."""

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import ROOT, get_settings

app = FastAPI(title="Questathon CRM")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"status": "syncing"})


@app.get("/api/health")
def health():
    s = get_settings()
    return {"ok": True, "live_mode": s.live_mode, "data_dir": s.data_dir.as_posix()}
