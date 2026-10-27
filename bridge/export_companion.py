#!/usr/bin/env python
"""Companion (astrbot_plugin_private_companion) -> JSONL exporter.

Read-only reader for the "我会永远陪着你" plugin database. Two ways to use it:

  1) as a CLI (writes a JSONL file + watermark):

       python export_companion.py --db <companion-v2.sqlite3> --out export.jsonl [--since <iso>]
       python export_companion.py --root <companion_data_root> --out export.jsonl

  2) as a module, imported by the AstrBot plugin astrbot-plugin/dsh_bridge_exporter
     (it calls `find_db()` and `export()` directly and serves them over HTTP).

The database is always opened with `mode=ro`, so this never writes to the
companion plugin's storage. Every record is one JSON line with a stable shape
used by sync.py and the DSH plugin:

  {"id","kind","content","occurred_at","updated_at","deleted",
   "role","peer","source","version","hash"}

Environment variables (all optional, CLI flags win):

  COMPANION_DB           path to companion-v2.sqlite3
  COMPANION_DATA_ROOT    data root to search for companion-v2.sqlite3
  DSH_BRIDGE_ROLE        label written into the `role` field (default: companion)
  DSH_BRIDGE_SOURCE      label written into the `source` field (default: qq/companion)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_GLOB = "companion-v2.sqlite3"
DEFAULT_ROLE = "companion"
DEFAULT_SOURCE = "qq/companion"
DEFAULT_OUT = Path("export") / "companion-export.jsonl"

# kind -> (table, id column). The content itself usually lives in a JSON column.
KINDS = {
    "memory": ("memory_atoms", "atom_id"),
    "experience": ("relationship_memories", None),
    "diary": ("role_journals", None),
    "life_event": ("role_activities", None),
    "relationship": ("relationship_memories", None),
}

DELETED_STATES = {"deleted", "removed", "archived", "revoked", "tombstone"}


def _role() -> str:
    return os.environ.get("DSH_BRIDGE_ROLE") or DEFAULT_ROLE


def _source() -> str:
    return os.environ.get("DSH_BRIDGE_SOURCE") or DEFAULT_SOURCE


def ro_uri(path: str) -> str:
    """SQLite read-only URI — the plugin's own storage is never written to."""
    p = Path(path).resolve()
    return p.as_uri() + "?mode=ro"


def find_db(root: str) -> str | None:
    """Locate companion-v2.sqlite3 under the companion plugin data root."""
    if not root:
        return None
    root_path = Path(root).expanduser()
    if root_path.is_file():
        return str(root_path)
    if not root_path.is_dir():
        return None
    for base, _dirs, files in os.walk(root_path):
        if DEFAULT_GLOB in files:
            return os.path.join(base, DEFAULT_GLOB)
    return None


def table_columns(con: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    except sqlite3.Error:
        return []


def pick(row: dict, *names: str, default=None):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return default


def parse_record(raw: str) -> dict:
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except Exception:
        return {}


def extract_content(kind: str, row: dict) -> str:
    """Pull human-readable text out of the record_json-style columns."""
    rec = parse_record(pick(row, "record_json", "payload_json", "document_json", default="{}") or "{}")
    for key in ("content", "text", "body", "summary", "value", "fact", "title", "note", "description"):
        val = rec.get(key) if isinstance(rec, dict) else None
        if isinstance(val, str) and val.strip():
            return val.strip()
    if isinstance(rec, dict):
        for val in rec.values():
            if isinstance(val, str) and len(val.strip()) > 4:
                return val.strip()
    for key in ("content", "text", "body", "summary", "value", "title", "note"):
        val = row.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def extract_view_content(doc: dict) -> str:
    """memory_views.document_json -> readable text (title + every statements[].text)."""
    if not isinstance(doc, dict):
        return ""
    title = str(doc.get("title") or "").strip()
    parts = [str(s.get("text") or "").strip() for s in (doc.get("statements") or []) if isinstance(s, dict)]
    body = " ".join([p for p in parts if p])
    if title and body:
        return f"{title}：{body}"
    return title or body


def make_hash(kind: str, rid: str, updated_at: str) -> str:
    """Stable change hash: same content-version -> same hash -> sync skips it."""
    return hashlib.sha256(f"{kind}|{rid}|{updated_at}".encode()).hexdigest()[:32]


def export(db_path: str, since: str | None = None) -> list[dict]:
    con = sqlite3.connect(ro_uri(db_path), uri=True)
    con.row_factory = sqlite3.Row
    out: list[dict] = []
    role, source = _role(), _source()

    def emit(kind: str, table: str, id_col: str | None, extra_where: str = ""):
        cols = table_columns(con, table)
        if not cols:
            return
        idc = id_col or ("id" if "id" in cols else cols[0])
        upd = next((c for c in ("updated_at", "modified_at", "created_at") if c in cols), None)
        occ = next((c for c in ("occurred_at", "created_at", "happened_at") if c in cols), None)
        state = "state" if "state" in cols else None
        sql = f"SELECT * FROM {table}"
        params: list = []
        conds = []
        if since and upd:
            conds.append(f"{upd} > ?")
            params.append(since)
        if extra_where:
            conds.append(extra_where)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        try:
            rows = con.execute(sql, params).fetchall()
        except sqlite3.Error as exc:  # table exists but shape differs
            print(f"[warn] {table}: {exc}", file=sys.stderr)
            return
        for row in rows:
            d = dict(row)
            rid = str(d.get(idc) or "")
            if not rid:
                continue
            st = str(d.get(state) or "").lower()
            deleted = st in DELETED_STATES
            updated = str(d.get(upd) or d.get("created_at") or "")
            occurred = str(d.get(occ) or updated)
            content = extract_content(kind, d)
            if not content and not deleted:
                continue
            out.append(
                {
                    "id": rid,
                    "kind": kind,
                    "content": content,
                    "occurred_at": occurred,
                    "updated_at": updated,
                    "deleted": deleted,
                    "role": role,
                    "peer": str(pick(d, "subject", "owner_key", "peer", default="")),
                    "source": source,
                    "version": str(pick(d, "revision", "version", default="1")),
                    "hash": make_hash(kind, rid, updated),
                }
            )

    emit("memory", "memory_atoms", "atom_id")
    emit("experience", "relationship_memories", None)
    emit("diary", "role_journals", None)
    emit("life_event", "role_activities", None)

    # 该插件 7.x 起，经历类记忆落在 memory_views（document_json.statements[].text），
    # memory_atoms 反而常为空；这里单独映射 experience 视图，人物卡/习惯等派生视图跳过。
    view_cols = table_columns(con, "memory_views")
    if view_cols:
        vsql = "SELECT * FROM memory_views"
        vparams: list = []
        vconds = []
        if since and "updated_at" in view_cols:
            vconds.append("updated_at > ?")
            vparams.append(since)
        if "state" in view_cols:
            vconds.append("state = 'active'")
        if vconds:
            vsql += " WHERE " + " AND ".join(vconds)
        for row in con.execute(vsql, vparams):
            d = dict(row)
            if str(d.get("kind") or "") != "experience":
                continue
            doc = parse_record(str(d.get("document_json") or "{}"))
            content = extract_view_content(doc)
            if not content:
                continue
            updated = str(d.get("updated_at") or doc.get("occurred_at") or "")
            occurred = str(doc.get("occurred_at") or updated)
            rid = "view:" + str(d.get("view_id") or "")
            out.append(
                {
                    "id": rid,
                    "kind": "experience",
                    "content": content,
                    "occurred_at": occurred,
                    "updated_at": updated,
                    "deleted": False,
                    "role": role,
                    "peer": "",
                    "source": source,
                    "version": str(d.get("revision") or "1"),
                    "hash": make_hash("experience", rid, updated),
                }
            )
    con.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="只读导出陪伴插件记忆为 JSONL")
    ap.add_argument("--db", default=os.environ.get("COMPANION_DB"), help="path to companion-v2.sqlite3")
    ap.add_argument(
        "--root",
        default=os.environ.get("COMPANION_DATA_ROOT"),
        help="<ASTROBOT_HOME>/data/plugin_data/astrbot_plugin_private_companion",
    )
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output JSONL path")
    ap.add_argument("--since", default=None, help="only rows updated after this ISO timestamp")
    ap.add_argument("--role", default=None, help='label for the role field, e.g. "companion(10000)" (env DSH_BRIDGE_ROLE)')
    ap.add_argument("--source", default=None, help='label for the source field (env DSH_BRIDGE_SOURCE)')
    args = ap.parse_args()

    if args.role:
        os.environ["DSH_BRIDGE_ROLE"] = args.role
    if args.source:
        os.environ["DSH_BRIDGE_SOURCE"] = args.source

    db = args.db or find_db(args.root)
    if not db or not os.path.exists(db):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "companion-v2.sqlite3 not found",
                    "root": args.root,
                    "hint": "传 --db 或 --root，或设置 COMPANION_DB / COMPANION_DATA_ROOT",
                },
                ensure_ascii=False,
            )
        )
        return 2

    records = export(db, args.since)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    Path(str(out) + ".watermark").write_text(stamp, encoding="utf-8")
    print(json.dumps({"ok": True, "db": db, "records": len(records), "out": str(out), "at": stamp}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())