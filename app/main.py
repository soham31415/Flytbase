"""FastAPI app: the web UI and JSON API. Run with exactly 1 worker."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import ROOT, get_settings
from app.store import Store


def load_store(settings):
    """Open the DB, add any snapshot payloads it doesn't hold yet, and rebuild."""
    store = Store(settings.data_dir / "crm.db")
    added = store.import_snapshots(settings.snapshot_dir)
    store.rebuild(settings.as_of)
    return store, added


@asynccontextmanager
async def lifespan(app):
    app.state.store, app.state.snapshots_added = load_store(get_settings())
    yield


app = FastAPI(title="Questathon CRM", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


def store(request: Request) -> Store:
    return request.app.state.store


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"status": "syncing"})


@app.get("/api/health")
def health(request: Request):
    s = get_settings()
    st = store(request)
    meta = st.meta()
    return {
        "ok": True,
        "live_mode": s.live_mode,
        "data_dir": s.data_dir.as_posix(),
        "as_of": meta.get("as_of"),
        "as_of_source": meta.get("as_of_source"),
        "last_sync": meta.get("last_sync"),
        "rebuilt_at": meta.get("rebuilt_at"),
        "accounts": meta["accounts"],
        "unresolved": meta["unresolved"],
        "counts": st.counts(),
    }


@app.get("/api/record/{key:path}")
def record(key: str, request: Request):
    """The full record behind any id. A deadline key contains '#': send it as %23."""
    rec = store(request).record(key)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"unknown id: {key}")
    return rec


@app.post("/api/reload")
def reload(request: Request):
    """Snapshot mode only: re-read the local snapshots after a pull, then rebuild. Never calls the MCP."""
    s = get_settings()
    if s.live_mode:
        raise HTTPException(status_code=409, detail="live mode: the scheduler keeps the data current")
    st = store(request)
    added = st.import_snapshots(s.snapshot_dir)
    st.rebuild(s.as_of)
    return {"ok": True, "snapshots_added": added, "as_of": st.meta().get("as_of")}
