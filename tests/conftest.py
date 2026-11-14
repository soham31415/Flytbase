import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
INDEX_MD = ROOT / "data" / "dump" / "index.md"

needs_snapshots = pytest.mark.skipif(
    not (RAW_DIR / "get_my_accounts.json").exists() or not INDEX_MD.exists(),
    reason="needs data/raw snapshots and data/dump/index.md (run scripts/pull.py and scripts/dump.py)",
)


def make_client(monkeypatch, tmp_path, snapshot_dir, live_mode="false"):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SNAPSHOT_DIR", str(snapshot_dir))
    monkeypatch.setenv("LIVE_MODE", live_mode)
    monkeypatch.setenv("AQ_TOKEN", "")  # tests never reach the real MCP: no token, no scheduler
    monkeypatch.delenv("AS_OF", raising=False)
    from app.main import app

    return TestClient(app)


@pytest.fixture
def empty_client(monkeypatch, tmp_path):
    """The app with no snapshots at all."""
    (tmp_path / "empty").mkdir()
    with make_client(monkeypatch, tmp_path, tmp_path / "empty") as c:
        yield c


@pytest.fixture
def client(monkeypatch, tmp_path):
    """The app loaded from the real snapshots in data/raw (DB in a temp dir)."""
    with make_client(monkeypatch, tmp_path, RAW_DIR) as c:
        yield c


def index_md_keys():
    """Every key in the "Every id" table of data/dump/index.md."""
    return set(re.findall(r"^\| ((?:[A-Za-z0-9]+|team|feature_requests)/[^ |]+) \| ", INDEX_MD.read_text(encoding="utf-8"), re.M))


def index_md_counts():
    """{(account, entity): count} from the counts table of data/dump/index.md."""
    text = INDEX_MD.read_text(encoding="utf-8")
    section = text.split("## Counts per entity type per account", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| ([^|]+) \| ([^|]+) \| (\d+) \|$", section, re.M)
    return {(a.strip(), e.strip()): int(n) for a, e, n in rows}
