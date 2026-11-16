from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.db import FIXTURES_DIR, connect


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


SCHEMA = """
CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  keeper TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '空闲'
);
CREATE TABLE IF NOT EXISTS zones (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
  id INTEGER PRIMARY KEY,
  biz_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  zone_id INTEGER REFERENCES zones(id),
  owner_shift_id INTEGER REFERENCES shifts(id),
  status TEXT NOT NULL DEFAULT '在途',
  note TEXT NOT NULL DEFAULT ''
);
-- 资格矩阵唯一来源：班次 × 责任区，qualified=1 可 / 0 否；缺行按“否”处理。
CREATE TABLE IF NOT EXISTS shift_qualifications (
  shift_id INTEGER NOT NULL REFERENCES shifts(id),
  zone_id INTEGER NOT NULL REFERENCES zones(id),
  qualified INTEGER NOT NULL CHECK (qualified IN (0, 1)),
  updated_at TEXT NOT NULL,
  PRIMARY KEY (shift_id, zone_id)
);
CREATE TABLE IF NOT EXISTS handovers (
  id INTEGER PRIMARY KEY,
  from_shift_id INTEGER NOT NULL REFERENCES shifts(id),
  to_shift_id INTEGER NOT NULL REFERENCES shifts(id),
  status TEXT NOT NULL,
  batch_count INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS handover_items (
  handover_id INTEGER NOT NULL REFERENCES handovers(id),
  batch_id INTEGER NOT NULL REFERENCES batches(id),
  PRIMARY KEY (handover_id, batch_id)
);
-- 发起交班时固化的双方 × 责任区资格快照；完成阶段只读此表，不读当前矩阵。
CREATE TABLE IF NOT EXISTS handover_qualification_snapshots (
  handover_id INTEGER NOT NULL REFERENCES handovers(id),
  shift_id INTEGER NOT NULL REFERENCES shifts(id),
  side TEXT NOT NULL CHECK (side IN ('from', 'to')),
  zone_id INTEGER NOT NULL REFERENCES zones(id),
  qualified INTEGER NOT NULL CHECK (qualified IN (0, 1)),
  captured_at TEXT NOT NULL,
  PRIMARY KEY (handover_id, shift_id, zone_id)
);
CREATE TRIGGER IF NOT EXISTS handover_snapshot_no_update
BEFORE UPDATE ON handover_qualification_snapshots
BEGIN
  SELECT RAISE(ABORT, '资格快照不可变，禁止改写');
END;
CREATE TRIGGER IF NOT EXISTS handover_snapshot_no_delete
BEFORE DELETE ON handover_qualification_snapshots
BEGIN
  SELECT RAISE(ABORT, '资格快照不可变，禁止删除');
END;
CREATE TABLE IF NOT EXISTS handover_start_rejections (
  id INTEGER PRIMARY KEY,
  from_shift_id INTEGER,
  to_shift_id INTEGER,
  reason TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconcile_reports (
  id INTEGER PRIMARY KEY,
  handover_id INTEGER REFERENCES handovers(id),
  before_count INTEGER NOT NULL,
  after_from_count INTEGER NOT NULL,
  after_to_count INTEGER NOT NULL,
  consistent INTEGER NOT NULL,
  detail_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate(conn) -> None:
    """兼容旧库：批次补责任区列；旧正在进行的交班单补资格快照。"""
    if "zone_id" not in _columns(conn, "batches"):
        conn.execute("ALTER TABLE batches ADD COLUMN zone_id INTEGER REFERENCES zones(id)")


def _seed_fixture(conn, data: dict) -> None:
    zones = data.get("zones", [])
    if zones and conn.execute("SELECT COUNT(*) AS c FROM zones").fetchone()["c"] == 0:
        for z in zones:
            conn.execute(
                "INSERT INTO zones(id, code, name) VALUES (?, ?, ?)",
                (z["id"], z["code"], z["name"]),
            )

    if data.get("shifts") and conn.execute("SELECT COUNT(*) AS c FROM shifts").fetchone()["c"] == 0:
        for s in data["shifts"]:
            conn.execute(
                "INSERT INTO shifts(id, name, keeper, status) VALUES (?, ?, ?, ?)",
                (s["id"], s["name"], s["keeper"], s["status"]),
            )

    if data.get("batches") and conn.execute("SELECT COUNT(*) AS c FROM batches").fetchone()["c"] == 0:
        for b in data["batches"]:
            conn.execute(
                """
                INSERT INTO batches(id, biz_key, title, zone_id, owner_shift_id, status, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    b["id"],
                    b["biz_key"],
                    b["title"],
                    b.get("zone_id"),
                    b["owner_shift_id"],
                    b["status"],
                    b.get("note", ""),
                ),
            )

    # 旧库批次没有责任区时归入首个责任区，保证“批次带责任区”不落空。
    first_zone = conn.execute("SELECT MIN(id) AS z FROM zones").fetchone()["z"]
    if first_zone is not None:
        conn.execute("UPDATE batches SET zone_id = ? WHERE zone_id IS NULL", (first_zone,))

    # 资格矩阵：先落种子行，再补齐所有 班次 × 责任区 缺省“否”，保证矩阵可完整维护。
    if (
        data.get("qualifications")
        and conn.execute("SELECT COUNT(*) AS c FROM shift_qualifications").fetchone()["c"] == 0
    ):
        now = _now()
        known_shifts = {
            r["id"] for r in conn.execute("SELECT id FROM shifts").fetchall()
        }
        known_zones = {r["id"] for r in conn.execute("SELECT id FROM zones").fetchall()}
        for q in data["qualifications"]:
            # 旧库可能只有部分班次，种子资格按现存班次/责任区收敛，避免外键失败。
            if q["shift_id"] not in known_shifts or q["zone_id"] not in known_zones:
                continue
            conn.execute(
                """
                INSERT INTO shift_qualifications(shift_id, zone_id, qualified, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(shift_id, zone_id) DO NOTHING
                """,
                (q["shift_id"], q["zone_id"], 1 if q["qualified"] else 0, now),
            )
    now = _now()
    pairs = conn.execute(
        """
        SELECT s.id AS shift_id, z.id AS zone_id
        FROM shifts s CROSS JOIN zones z
        """
    ).fetchall()
    for p in pairs:
        conn.execute(
            """
            INSERT INTO shift_qualifications(shift_id, zone_id, qualified, updated_at)
            VALUES (?, ?, 0, ?)
            ON CONFLICT(shift_id, zone_id) DO NOTHING
            """,
            (p["shift_id"], p["zone_id"], now),
        )

    _backfill_in_progress_snapshots(conn)


def _backfill_in_progress_snapshots(conn) -> None:
    """旧库遗留的进行中交班单：按当时当前矩阵补一份快照，避免升级后无法完成。"""
    now = _now()
    active = conn.execute(
        "SELECT id, from_shift_id, to_shift_id FROM handovers WHERE status = '进行中'"
    ).fetchall()
    for h in active:
        exists = conn.execute(
            "SELECT 1 FROM handover_qualification_snapshots WHERE handover_id = ? LIMIT 1",
            (h["id"],),
        ).fetchone()
        if exists:
            continue
        matrix = conn.execute(
            "SELECT shift_id, zone_id, qualified FROM shift_qualifications"
        ).fetchall()
        qmap = {(r["shift_id"], r["zone_id"]): r["qualified"] for r in matrix}
        zones = conn.execute("SELECT id FROM zones ORDER BY id").fetchall()
        for side, sid in (("from", h["from_shift_id"]), ("to", h["to_shift_id"])):
            for z in zones:
                conn.execute(
                    """
                    INSERT INTO handover_qualification_snapshots(
                      handover_id, shift_id, side, zone_id, qualified, captured_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (h["id"], sid, side, z["id"], qmap.get((sid, z["id"]), 0), now),
                )


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        seed_path = FIXTURES_DIR / "seed.json"
        data = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        _seed_fixture(conn, data)
        conn.commit()
