#!/usr/bin/env python
"""End-to-end test for the export -> sync pipeline (pure synthetic data).

Builds a 34-record JSONL fixture in a throwaway directory (new / duplicate /
cross-role / deleted records), runs sync.py against a throwaway state DB, then
exercises the incremental paths:

    python bridge/fixtures/run_tests.py

Exit code 0 = all four steps matched the expected counters.
Scratch files go to %TEMP%; set DSH_BRIDGE_TEST_DIR to use another directory.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGE = HERE.parent
sys.path.insert(0, str(BRIDGE))

import sync as syncmod  # noqa: E402

ISO = "2026-01-1{}T09:00:00+08:00"
ROLE = "companion(10000)"
PEER = "user(10001)"

EXPECTED = [
    # (inserted, updated, deleted, skipped)
    (32, 0, 0, 2),   # 1 full sync: 34 lines, 1 exact duplicate + 1 never-seen deletion
    (0, 0, 0, 34),   # 2 repeat sync: everything deduped
    (0, 1, 0, 32),   # 3 modify one record
    (0, 0, 1, 32),   # 4 delete one record -> tombstone
]


def rec(rid, kind, content, i, deleted=False, role=ROLE, peer=PEER):
    return {
        "id": rid,
        "kind": kind,
        "content": content,
        "occurred_at": ISO.format(i % 10),
        "updated_at": ISO.format(i % 10),
        "deleted": deleted,
        "role": role,
        "peer": peer,
        "source": "qq/companion",
        "version": "1",
        "hash": f"h{rid}-{i}",
    }


def build_fixture() -> list[dict]:
    rows: list[dict] = []
    kinds = ["memory", "experience", "diary", "life_event", "relationship"]
    for i in range(30):
        rows.append(rec(f"m{i:03d}", kinds[i % len(kinds)], f"示例记忆 {i}：用户和助手在周{i % 7}聊过的第 {i} 件事", i))
    rows.append(rec("m000", "memory", "示例记忆 0：用户和助手在周0聊过的第 0 件事", 0))  # exact duplicate line
    rows.append(rec("x001", "experience", "跨角色记忆：另一个角色与用户的约定", 3, role="other(20000)"))
    rows.append(rec("x002", "life_event", "跨角色生活事件：另一个角色在画画", 4, role="other(20000)"))
    rows.append(rec("d001", "memory", "这条已被删除的记忆", 5, deleted=True))
    return rows


def write(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def q(state: Path, sql: str):
    con = sqlite3.connect(state)
    try:
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()


def main() -> int:
    scratch = os.environ.get("DSH_BRIDGE_TEST_DIR") or tempfile.gettempdir()
    Path(scratch).mkdir(parents=True, exist_ok=True)
    tag = f"{os.getpid()}"
    fixtures = Path(scratch) / f"companion-export-{tag}.jsonl"
    state = Path(scratch) / f"test-sync-state-{tag}.db"
    report: list[str] = []
    ok = True
    try:
        rows = build_fixture()
        write(rows, fixtures)
        report.append(f"fixture lines: {len(rows)}")

        runs = []
        runs.append(syncmod.sync(str(state), str(fixtures)))

        runs.append(syncmod.sync(str(state), str(fixtures)))

        rows = rows[:30] + rows[31:]  # drop the duplicated line first
        rows[0] = dict(rows[0], content="示例记忆 0（已修订）：改成新的事实",
                       updated_at="2026-01-12T09:00:00+08:00", hash="h-m000-v2")
        write(rows, fixtures)
        runs.append(syncmod.sync(str(state), str(fixtures)))

        write([dict(rows[0], deleted=True, updated_at="2026-01-13T09:00:00+08:00", hash="h-m000-del")] + rows[1:], fixtures)
        runs.append(syncmod.sync(str(state), str(fixtures)))

        for idx, (run, expected) in enumerate(zip(runs, EXPECTED), start=1):
            got = (run["inserted"], run["updated"], run["deleted"], run["skipped"])
            flag = "ok" if got == expected else "MISMATCH"
            ok = ok and got == expected
            report.append(
                f"{idx} step {idx} -> inserted={got[0]} updated={got[1]} deleted={got[2]} "
                f"skipped={got[3]} [{flag}]"
            )

        active = q(state, "SELECT COUNT(*) FROM memories WHERE deleted=0")
        tombstones = q(state, "SELECT COUNT(*) FROM memories WHERE deleted=1")
        logs = q(state, "SELECT COUNT(*) FROM sync_log")
        dupes = q(state, "SELECT COUNT(*) FROM memories WHERE id='m000'")
        checks = [(active, 31), (tombstones, 1), (logs, 4), (dupes, 1)]
        ok = ok and all(got == want for got, want in checks)
        report.append(f"   rows in db    -> active={active} deleted={tombstones} (expect 31/1)")
        report.append(f"   log rows      -> {logs} (expect 4)")
        report.append(f"   dupes kept    -> {dupes} (expect 1)")

        # dry-run must not change counters
        before = q(state, "SELECT COUNT(*) FROM sync_log")
        syncmod.sync(str(state), str(fixtures), dry_run=True)
        after = q(state, "SELECT COUNT(*) FROM sync_log")
        ok = ok and before == after
        report.append(f"   dry-run       -> sync_log rows {before} -> {after} (expect unchanged)")
    finally:
        for p in (fixtures, state, Path(str(state) + "-journal")):
            try:
                p.unlink()
            except OSError:
                pass

    report.append("SYNC TEST " + ("OK" if ok else "FAILED"))
    print("\n".join(report))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())