"""FastAPI app: the web UI and JSON API. Run with exactly 1 worker."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import ROOT, get_settings
from app.engine.run import evaluate, resolve_metric
from app.store import Store


def refresh_state(app, store, settings):
    """Rebuild the store's tables and re-run the engine. Called after every data change."""
    world, _ = store.rebuild(settings.as_of)
    app.state.world = world
    app.state.engine = evaluate(world, store.meta().get("as_of"))


@asynccontextmanager
async def lifespan(app):
    settings = get_settings()
    store = Store(settings.data_dir / "crm.db")
    app.state.store = store
    app.state.snapshots_added = store.import_snapshots(settings.snapshot_dir)
    refresh_state(app, store, settings)
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
    refresh_state(request.app, st, s)
    return {"ok": True, "snapshots_added": added, "as_of": st.meta().get("as_of")}


def engine_account(request, account):
    acct = request.app.state.engine["accounts"].get(account)
    if acct is None:
        raise HTTPException(status_code=404, detail=f"unknown account: {account}")
    return acct


@app.get("/api/claims/{account}")
def claims(account: str, request: Request):
    """Every claim for an account with its verdict, numbers, evidence, causes, impact and who to ask."""
    acct = engine_account(request, account)
    return {"account": account, "as_of": request.app.state.engine["as_of"], "claims": acct["claims"]}


@app.get("/api/metrics/{account}")
def metrics(account: str, request: Request):
    acct = engine_account(request, account)
    return {"account": account, "as_of": request.app.state.engine["as_of"],
            "metrics": [{k: m[k] for k in ("name", "value", "definition", "sources")} | {"row_count": len(m["rows"])} for m in acct["metrics"]]}


@app.get("/api/metrics/{account}/{name}")
def metric_trace(account: str, name: str, request: Request):
    """Trace a metric: its definition and the exact rows it counted, each resolved to its content."""
    engine_account(request, account)
    m = resolve_metric(request.app.state.engine, request.app.state.world, account, name, store(request).record)
    if m is None:
        raise HTTPException(status_code=404, detail=f"unknown metric: {account}/{name}")
    return m
