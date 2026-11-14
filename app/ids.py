"""Every citable id, keyed "<scope>/<id>" (CLAUDE.md "Ids and citations"). Pure logic.

Built from app.world.assemble(...). scripts/dump.py writes it to data/dump/index.md and
app/store.py loads it into the id_index table, so both always agree.
"""

from app.world import csv_rows

# Row ids inside structured paperwork: kind -> (list key, id field, index kind)
ROW_IDS = {
    "tickets": ("tickets", "id", "ticket"),
    "invoices": ("invoices", "number", "invoice"),
    "devices": ("docks", "dock_id", "dock"),
    "users": ("users", "user_id", "user"),
    "mail_log": ("mail", "thread", "mail_thread"),
    "profile": ("contacts_on_file", "id", "contact"),  # a stakeholder with the same id stays primary
}
# CSV kind -> (id column, index kind). Signals label a requests.csv row as kind "request".
CSV_ROW_IDS = {"requests": ("request_id", "request"), "request": ("request_id", "request")}


class Index:
    def __init__(self):
        self.entries = {}  # key -> {key, id, kind, scope, label, file, where, record}
        self.collisions = []  # (key, kind kept, other kind, where)

    def add(self, ident, kind, scope, label, file, where, record=None):
        key = f"{scope}/{ident}"
        if key in self.entries:
            if self.entries[key]["kind"] != kind and (self.entries[key]["kind"], kind) != ("stakeholder", "contact"):
                self.collisions.append((key, self.entries[key]["kind"], kind, f"{file} {where}"))
            return
        self.entries[key] = {
            "key": key, "id": str(ident), "kind": kind, "scope": scope,
            "label": (label or "")[:90], "file": file, "where": where, "record": record,
        }

    def get(self, scope, ident):
        return self.entries.get(f"{scope}/{ident}")

    def label(self, scope, ident):
        e = self.get(scope, ident)
        return f"`{scope}/{ident}` ({e['kind']}: {e['label']})" if e else f"`{scope}/{ident}` (**not found**)"


def deadline_key(opp_id):
    return f"{opp_id}#deadline"


def is_real_id(value):
    """False for empty cells and placeholders such as "(none)", which must never become ids."""
    v = (value or "").strip()
    return bool(v) and not (v.startswith("(") and v.endswith(")"))


def build(world):
    idx = Index()
    for a in world["accounts"]:
        A, src = a["id"], a.get("sheet_source") or "whoami"
        summary = {k: v for k, v in a.items() if k not in ("stakeholders", "facts", "opportunities", "offers", "deadlines")}
        idx.add(A, "account", A, a.get("name"), src, "account", summary)
        for j, s in enumerate(a.get("stakeholders", [])):
            idx.add(s["id"], "stakeholder", A, f"{s['name']}, {s['role']}", src, f"stakeholders[{j}]", s)
        for j, f in enumerate(a.get("facts", [])):
            idx.add(f["id"], "fact", A, f"[{f['status']}] {f['text']}", src, f"facts[{j}]", f)
        for j, o in enumerate(a.get("opportunities", [])):
            idx.add(o["id"], "opportunity", A, f"[{o['stage']}] {o['title']}", src, f"opportunities[{j}]", o)
            if o.get("deadline"):
                idx.add(deadline_key(o["id"]), "deadline", A, o["deadline"].get("text"), src,
                        f"opportunities[{j}].deadline", {**o["deadline"], "opportunity": f"{A}/{o['id']}"})
        for j, o in enumerate(a.get("offers", [])):
            idx.add(o["id"], "offer", A, f"[{o['status']}] {o['title']}", src, f"offers[{j}]", o)
    for f in world["files"]:
        idx.add(f["id"], "file", f.get("account"), f"{f.get('title')} ({f.get('path')})", f["source"], "files", f)

    # JSON rows (tickets, ...) before CSV rows, so a ticket is the primary entry for an id
    # that requests.csv also lists.
    for csv_pass in (False, True):
        for A, records in world["paperwork"].items():
            for j, r in enumerate(records):
                pid = f"{A}/{r['path']}"
                if not csv_pass:
                    idx.add(r["path"], f"paperwork:{r['kind']}", A, r["path"], r["source"], f"records[{j}]",
                            {k: v for k, v in r.items() if k != "rows"})
                add_row_ids(idx, A, pid, r, f"records[{j}]", csv_pass)

    for j, s in enumerate(world["signals"]):
        idx.add(s["id"], "signal", s.get("account"), f"{s.get('dated', '')} {s.get('title', '')}", "get_signals", f"signals[{j}]", s)
    for j, p in enumerate(world["team"]):
        idx.add(p["id"], "team_member", "team", f"{p['name']}, {p['role']}", "get_flytbase_team", f"people[{j}]", p)
    for j, fr in enumerate(world["feature_requests"]):
        idx.add(fr["id"], "feature_request", "feature_requests", f"[{fr.get('status')}] {fr.get('title')}", "get_feature_requests", f"requests[{j}]", fr)
    return idx


def add_row_ids(idx, A, pid, rec, where, csv_pass):
    """Index ids of rows inside a paperwork record (tickets, invoices, docks, users, ...)."""
    kind = rec.get("kind")
    data = rec.get("data")
    if not csv_pass and kind in ROW_IDS and isinstance(data, dict):
        list_key, id_field, row_kind = ROW_IDS[kind]
        for k, row in enumerate(data.get(list_key, [])):
            if is_real_id(str(row.get(id_field) or "")):
                label = row.get("subject") or row.get("what") or row.get("name") or row.get("role") or row.get("model") or ""
                idx.add(row[id_field], row_kind, A, f"{label} (in {pid})", rec["source"], f"{where}.data.{list_key}[{k}]",
                        {**row, "in": pid})
    if csv_pass and kind in CSV_ROW_IDS and isinstance(rec.get("text"), str):
        col, row_kind = CSV_ROW_IDS[kind]
        rows = rec.get("rows") or [{"row": row, "source": rec["source"]} for row in csv_rows(rec["text"])]
        for k, r in enumerate(rows):
            row = r["row"]
            if is_real_id(row.get(col)):
                idx.add(row[col], row_kind, A, f"{row.get('summary', '')} (in {pid})", r["source"], f"{where} row {k + 1}",
                        {**row, "in": pid, "source": r["source"]})
