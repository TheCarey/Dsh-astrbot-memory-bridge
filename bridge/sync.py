#!/usr/bin/env python
"""Companion export -> sync-state.db (the database the DSH plugin reads).

Reads JSONL produced by export_companion.py (a file path or the exporter's HTTP
endpoint) and upserts it into sync-state.db. Never touches the companion plugin's
own database and never touches any other memory store.

Usage:
    python sync.py --input export.jsonl
    python sync.py --url http://127.0.0.1:6197/export --token <LOCAL_TOKEN>
    python sync.py --state ~/.dsh-bridge/sync-state.db --input export.jsonl --dry-run

Behaviour:
  * new record            -> INSERT
  * same hash             -> SKIP (dedup)
  * same id, new hash     -> UPDATE
  * record.deleted = true -> mark deleted = 1 (tombstone), keep row for audit
  * every run appends a row to sync_log

Environment variables (CLI flags win):
  DSH_SYNC_STATE   state db path      (default ~/.dsh-bridge/sync-state.db)
  DSH_SYNC_INPUT   input jsonl path   (default ./export/companion-export.jsonl)
  DSH_BRIDGE_URL   exporter endpoint  (used when --url is omitted but env is set)
  DSH_BRIDGE_TOKEN exporter token
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

DEFAULT_STATE = Path.home() / ".dsh-bridge" / "sync-state.db"
DEFAULT_INPUT = Path("export") / "companion-export.jsonl"
DEFAULT_TIMEOUT = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    content      TEXT NOT NULL,
    occurred_at  TEXT,
    updated_at   TEXT,
    deleted      INTEGER NOT NULL DEFAULT 0,
    role         TEXT,
    peer         TEXT,
    source       TEXT,
    version      TEXT,
    hash         TEXT NOT NULL,
    first_seen   TEXT NOT NULL,
    last_sync    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memories_kind_idx ON memories(kind, deleted, updated_at);
CREATE TABLE IF NOT EXISTS sync_log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    source     TEXT,
    inserted   INTEGER NOT NULL DEFAULT 0,
    updated    INTEGER NOT NULL DEFAULT 0,
    deleted    INTEGER NOT NULL DEFAULT 0,
    skipped    INTEGER NOT NULL DEFAULT 0,
    errors     INTEGER NOT NULL DEFAULT 0,
    note       TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("id"):
                rows.append(rec)
    return rows


def fetch(url: str, token: str | None = None, since: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    """Pull JSONL straight from the AstrBot exporter endpoint (GET /export)."""
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    if token:
        query.setdefault("token", token)
    if since:
        query.setdefault("since", since)
    parts = urllib.parse.urlparse(url)
    full = parts._replace(query=urllib.parse.urlencode(query)).geturl()
    req = urllib.request.Request(full, headers={"Accept": "application/x-ndjson"})
    if token:
        req.add_header("X-Bridge-Token", token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - local endpoint
        body = resp.read().decode("utf-8", "replace")
    rows: list[dict] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("id"):
            rows.append(rec)
    return rows


def sync(state_path: str, input_path: str | None = None, dry_run: bool = False, records: list[dict] | None = None,
         source_label: str | None = None) -> dict:
    Path(state_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(Path(state_path).expanduser()))
    con.executescript(SCHEMA)
    con.row_factory = sqlite3.Row

    if records is None:
        records = load(Path(input_path)) if input_path else []
    label = source_label or (str(input_path) if input_path else "remote")
    stats = {"input": len(records), "inserted": 0, "updated": 0, "deleted": 0, "skipped": 0, "errors": 0}

    for rec in records:
        try:
            rid = str(rec["id"])
            kind = str(rec.get("kind") or "memory")
            content = str(rec.get("content") or "").strip()
            deleted = 1 if rec.get("deleted") else 0
            if not content and not deleted:
                stats["skipped"] += 1
                continue
            rhash = str(rec.get("hash") or "")
            stamp = now()
            cur = con.execute("SELECT hash, deleted FROM memories WHERE id = ?", (rid,)).fetchone()

            if cur is None:
                if deleted:
                    stats["skipped"] += 1
                    continue
                stats["inserted"] += 1
                if not dry_run:
                    con.execute(
                        "INSERT INTO memories (id,kind,content,occurred_at,updated_at,deleted,role,peer,source,version,hash,first_seen,last_sync)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (rid, kind, content, rec.get("occurred_at"), rec.get("updated_at"), 0,
                         rec.get("role"), rec.get("peer"), rec.get("source"), rec.get("version"),
                         rhash, stamp, stamp),
                    )
                continue

            if cur["hash"] == rhash and int(cur["deleted"]) == deleted:
                stats["skipped"] += 1
                if not dry_run:
                    con.execute("UPDATE memories SET last_sync = ? WHERE id = ?", (stamp, rid))
                continue

            if deleted:
                stats["deleted"] += 1
            else:
                stats["updated"] += 1
            if not dry_run:
                con.execute(
                    "UPDATE memories SET kind=?, content=?, occurred_at=?, updated_at=?, deleted=?,"
                    " role=?, peer=?, source=?, version=?, hash=?, last_sync=? WHERE id=?",
                    (kind, content, rec.get("occurred_at"), rec.get("updated_at"),
                     deleted, rec.get("role"), rec.get("peer"), rec.get("source"), rec.get("version"),
                     rhash, stamp, rid),
                )
        except Exception:  # keep going, count the failure
            stats["errors"] += 1

    if not dry_run:
        con.execute(
            "INSERT INTO sync_log (at,source,inserted,updated,deleted,skipped,errors,note) VALUES (?,?,?,?,?,?,?,?)",
            (now(), label, stats["inserted"], stats["updated"], stats["deleted"], stats["skipped"], stats["errors"], "run"),
        )
        con.execute("INSERT INTO meta(key,value) VALUES('last_sync',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now(),))
        con.execute("INSERT INTO meta(key,value) VALUES('last_input',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (label,))
    con.commit()
    stats["active"] = con.execute("SELECT COUNT(*) FROM memories WHERE deleted = 0").fetchone()[0]
    stats["tombstones"] = con.execute("SELECT COUNT(*) FROM memories WHERE deleted = 1").fetchone()[0]
    stats["state"] = str(Path(state_path).expanduser())
    con.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="把陪伴插件导出同步进 sync-state.db")
    ap.add_argument("--state", default=os.environ.get("DSH_SYNC_STATE") or str(DEFAULT_STATE))
    ap.add_argument("--input", default=os.environ.get("DSH_SYNC_INPUT") or str(DEFAULT_INPUT))
    ap.add_argument("--url", default=os.environ.get("DSH_BRIDGE_URL"), help="exporter 端点，如 http://127.0.0.1:6197/export")
    ap.add_argument("--token", default=os.environ.get("DSH_BRIDGE_TOKEN"), help="exporter 访问令牌")
    ap.add_argument("--since", default=None, help="只拉取该 ISO 时间之后更新的记录")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.url:
        try:
            records = fetch(args.url, args.token, args.since)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": f"fetch failed: {exc}", "url": args.url}, ensure_ascii=False))
            return 2
        result = sync(args.state, None, args.dry_run, records=records, source_label=args.url)
    else:
        if not Path(args.input).exists():
            print(json.dumps({"ok": False, "error": "input not found", "input": args.input}, ensure_ascii=False))
            return 2
        result = sync(args.state, args.input, args.dry_run)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())