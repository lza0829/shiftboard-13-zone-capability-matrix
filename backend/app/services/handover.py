from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db import connect, rows_to_dicts


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


QUALIFIED_LABEL = {1: "可", 0: "否"}
SIDE_LABEL = {"from": "交班方", "to": "接班方"}


def _qualification_label(value: int | None) -> str:
    return QUALIFIED_LABEL.get(1 if value else 0, "否")


def list_shifts() -> list[dict]:
    with connect() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM shifts ORDER BY id").fetchall())


def list_batches(owner_shift_id: int | None = None) -> list[dict]:
    with connect() as conn:
        sql = """
            SELECT b.*, s.name AS owner_shift_name,
                   z.code AS zone_code, z.name AS zone_name
            FROM batches b
            LEFT JOIN shifts s ON s.id = b.owner_shift_id
            LEFT JOIN zones z ON z.id = b.zone_id
        """
        params: tuple = ()
        if owner_shift_id is not None:
            sql += " WHERE b.owner_shift_id = ?"
            params = (owner_shift_id,)
        sql += " ORDER BY b.id"
        rows = conn.execute(sql, params).fetchall()
        return rows_to_dicts(rows)


def list_zones() -> list[dict]:
    with connect() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM zones ORDER BY id").fetchall())


# ---------------------------------------------------------------- 资格矩阵

def _matrix_map(conn) -> dict[tuple[int, int], int]:
    rows = conn.execute(
        "SELECT shift_id, zone_id, qualified FROM shift_qualifications"
    ).fetchall()
    return {(r["shift_id"], r["zone_id"]): r["qualified"] for r in rows}


def get_qualifications() -> dict:
    """资格矩阵唯一读取入口：班次 × 责任区，缺行视为“否”。"""
    with connect() as conn:
        shifts = rows_to_dicts(conn.execute("SELECT * FROM shifts ORDER BY id").fetchall())
        zones = rows_to_dicts(conn.execute("SELECT * FROM zones ORDER BY id").fetchall())
        matrix = _matrix_map(conn)
        cells = []
        for s in shifts:
            for z in zones:
                q = matrix.get((s["id"], z["id"]), 0)
                cells.append(
                    {
                        "shift_id": s["id"],
                        "shift_name": s["name"],
                        "zone_id": z["id"],
                        "zone_code": z["code"],
                        "zone_name": z["name"],
                        "qualified": bool(q),
                        "资格": _qualification_label(q),
                    }
                )
        return {"班次": shifts, "责任区": zones, "矩阵": cells}


def set_qualification(shift_id: int, zone_id: int, qualified: bool) -> dict:
    with connect() as conn:
        s = conn.execute("SELECT * FROM shifts WHERE id = ?", (shift_id,)).fetchone()
        z = conn.execute("SELECT * FROM zones WHERE id = ?", (zone_id,)).fetchone()
        if not s:
            raise HTTPException(404, "班次不存在")
        if not z:
            raise HTTPException(404, "责任区不存在")
        conn.execute(
            """
            INSERT INTO shift_qualifications(shift_id, zone_id, qualified, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(shift_id, zone_id)
            DO UPDATE SET qualified = excluded.qualified, updated_at = excluded.updated_at
            """,
            (shift_id, zone_id, 1 if qualified else 0, _now()),
        )
        conn.commit()
    return {
        "shift_id": shift_id,
        "zone_id": zone_id,
        "qualified": bool(qualified),
        "资格": _qualification_label(1 if qualified else 0),
    }


def list_snapshots(handover_id: int | None = None) -> list[dict]:
    with connect() as conn:
        sql = """
            SELECT snap.handover_id, snap.side, snap.shift_id, s.name AS shift_name,
                   snap.zone_id, z.code AS zone_code, z.name AS zone_name,
                   snap.qualified, snap.captured_at
            FROM handover_qualification_snapshots snap
            JOIN shifts s ON s.id = snap.shift_id
            JOIN zones z ON z.id = snap.zone_id
        """
        params: tuple = ()
        if handover_id is None:
            sql += """
                WHERE snap.handover_id IN (
                    SELECT id FROM handovers ORDER BY id DESC LIMIT 10
                )
            """
        else:
            sql += " WHERE snap.handover_id = ?"
            params = (handover_id,)
        sql += " ORDER BY snap.handover_id DESC, snap.side, snap.zone_id"
        rows = rows_to_dicts(conn.execute(sql, params).fetchall())
        for r in rows:
            r["双方"] = SIDE_LABEL.get(r["side"], r["side"])
            r["资格"] = _qualification_label(r["qualified"])
            r["qualified"] = bool(r["qualified"])
        return rows


def list_start_rejections() -> list[dict]:
    with connect() as conn:
        rows = rows_to_dicts(
            conn.execute(
                "SELECT * FROM handover_start_rejections ORDER BY id DESC"
            ).fetchall()
        )
        for r in rows:
            try:
                r["detail"] = json.loads(r.pop("detail_json"))
            except json.JSONDecodeError:
                r["detail"] = {}
        return rows


# ---------------------------------------------------------------- 交班单

def list_handovers() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT h.*,
                   fs.name AS from_shift_name,
                   ts.name AS to_shift_name
            FROM handovers h
            JOIN shifts fs ON fs.id = h.from_shift_id
            JOIN shifts ts ON ts.id = h.to_shift_id
            ORDER BY h.id DESC
            """
        ).fetchall()
        return rows_to_dicts(rows)


def get_handover(handover_id: int) -> dict:
    with connect() as conn:
        h = conn.execute(
            """
            SELECT h.*,
                   fs.name AS from_shift_name,
                   ts.name AS to_shift_name
            FROM handovers h
            JOIN shifts fs ON fs.id = h.from_shift_id
            JOIN shifts ts ON ts.id = h.to_shift_id
            WHERE h.id = ?
            """,
            (handover_id,),
        ).fetchone()
        if not h:
            raise HTTPException(404, "交班单不存在")
        items = conn.execute(
            """
            SELECT b.*, z.code AS zone_code, z.name AS zone_name
            FROM handover_items hi
            JOIN batches b ON b.id = hi.batch_id
            LEFT JOIN zones z ON z.id = b.zone_id
            WHERE hi.handover_id = ?
            ORDER BY b.id
            """,
            (handover_id,),
        ).fetchall()
        out = dict(h)
        out["items"] = rows_to_dicts(items)
        out["资格快照"] = list_snapshots(handover_id)
        return out


def dashboard() -> dict:
    with connect() as conn:
        shifts = rows_to_dicts(conn.execute("SELECT * FROM shifts ORDER BY id").fetchall())
        inflight = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE status = '在途'"
        ).fetchone()["c"]
        active = conn.execute(
            "SELECT COUNT(*) AS c FROM handovers WHERE status = '进行中'"
        ).fetchone()["c"]
        last = conn.execute(
            "SELECT * FROM handovers ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "班次数": len(shifts),
            "在途批次数": inflight,
            "进行中交班": active,
            "最近交班": dict(last) if last else None,
            "班次": shifts,
        }


def _log_start_rejection(
    conn,
    from_shift_id: int | None,
    to_shift_id: int | None,
    reason: str,
    detail: dict,
) -> None:
    conn.execute(
        """
        INSERT INTO handover_start_rejections(from_shift_id, to_shift_id, reason, detail_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            from_shift_id,
            to_shift_id,
            reason,
            json.dumps(detail, ensure_ascii=False),
            _now(),
        ),
    )
    conn.commit()


def start_handover(from_shift_id: int, to_shift_id: int) -> dict:
    with connect() as conn:
        if from_shift_id == to_shift_id:
            reason = "交班双方不能是同一班次"
            _log_start_rejection(
                conn,
                from_shift_id,
                to_shift_id,
                reason,
                {"阶段": "参数校验", "原因": reason},
            )
            raise HTTPException(400, reason)

        fs = conn.execute("SELECT * FROM shifts WHERE id = ?", (from_shift_id,)).fetchone()
        ts = conn.execute("SELECT * FROM shifts WHERE id = ?", (to_shift_id,)).fetchone()
        if not fs or not ts:
            reason = "班次不存在"
            _log_start_rejection(
                conn,
                from_shift_id,
                to_shift_id,
                reason,
                {"阶段": "参数校验", "原因": reason},
            )
            raise HTTPException(404, reason)

        active = conn.execute(
            "SELECT id FROM handovers WHERE status = '进行中' LIMIT 1"
        ).fetchone()
        if active:
            reason = f"已有进行中的交班单#{active['id']}，禁止并行交班"
            _log_start_rejection(
                conn,
                from_shift_id,
                to_shift_id,
                reason,
                {"阶段": "互斥校验", "进行中交班单": active["id"]},
            )
            raise HTTPException(409, reason)

        batches = rows_to_dicts(
            conn.execute(
                """
                SELECT b.id, b.biz_key, b.title, b.zone_id,
                       z.code AS zone_code, z.name AS zone_name
                FROM batches b
                LEFT JOIN zones z ON z.id = b.zone_id
                WHERE b.owner_shift_id = ? AND b.status = '在途'
                ORDER BY b.id
                """,
                (from_shift_id,),
            ).fetchall()
        )
        if not batches:
            reason = "交班方没有在途批次可交接"
            _log_start_rejection(
                conn,
                from_shift_id,
                to_shift_id,
                reason,
                {"阶段": "清单校验", "原因": reason},
            )
            raise HTTPException(400, reason)

        # 清单内每个责任区都必须被双方覆盖，禁止只看接班方或只看第一区。
        matrix = _matrix_map(conn)
        zone_ids = sorted({b["zone_id"] for b in batches if b["zone_id"] is not None})
        unzoned = [b["biz_key"] for b in batches if b["zone_id"] is None]
        conflicts = []
        for zid in zone_ids:
            for side, sid, name in (
                ("from", from_shift_id, fs["name"]),
                ("to", to_shift_id, ts["name"]),
            ):
                q = matrix.get((sid, zid), 0)
                if q != 1:
                    conflicts.append(
                        {
                            "side": side,
                            "双方": SIDE_LABEL[side],
                            "shift_id": sid,
                            "shift_name": name,
                            "zone_id": zid,
                            "qualified": bool(q),
                            "资格": _qualification_label(q),
                            "批次": [
                                b["biz_key"] for b in batches if b["zone_id"] == zid
                            ],
                        }
                    )
        if unzoned:
            reason = "以下批次未标责任区，无法校验资格：" + "、".join(unzoned)
            _log_start_rejection(
                conn,
                from_shift_id,
                to_shift_id,
                reason,
                {
                    "阶段": "资格校验",
                    "原因": reason,
                    "交班方": {"id": from_shift_id, "name": fs["name"]},
                    "接班方": {"id": to_shift_id, "name": ts["name"]},
                    "清单": batches,
                },
            )
            raise HTTPException(409, reason)
        if conflicts:
            parts = []
            for c in conflicts:
                z = next(b for b in batches if b["zone_id"] == c["zone_id"])
                parts.append(
                    f"{c['双方']}「{c['shift_name']}」对责任区「{z['zone_name']}」资格=否"
                )
            reason = "资格校验未通过，整单拒绝：" + "；".join(parts)
            _log_start_rejection(
                conn,
                from_shift_id,
                to_shift_id,
                reason,
                {
                    "阶段": "资格校验",
                    "原因": reason,
                    "交班方": {"id": from_shift_id, "name": fs["name"]},
                    "接班方": {"id": to_shift_id, "name": ts["name"]},
                    "清单": batches,
                    "冲突": conflicts,
                },
            )
            raise HTTPException(409, reason)

        now = _now()
        cur = conn.execute(
            """
            INSERT INTO handovers(from_shift_id, to_shift_id, status, batch_count, started_at)
            VALUES (?, ?, '进行中', ?, ?)
            """,
            (from_shift_id, to_shift_id, len(batches), now),
        )
        hid = cur.lastrowid
        for b in batches:
            conn.execute(
                "INSERT INTO handover_items(handover_id, batch_id) VALUES (?, ?)",
                (hid, b["id"]),
            )
        # 发起即固化「双方 × 责任区」资格快照，完成阶段只解释快照。
        zones = conn.execute("SELECT id FROM zones ORDER BY id").fetchall()
        for side, sid in (("from", from_shift_id), ("to", to_shift_id)):
            for z in zones:
                conn.execute(
                    """
                    INSERT INTO handover_qualification_snapshots(
                      handover_id, shift_id, side, zone_id, qualified, captured_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (hid, sid, side, z["id"], matrix.get((sid, z["id"]), 0), now),
                )
        conn.execute("UPDATE shifts SET status = '交班中' WHERE id = ?", (from_shift_id,))
        conn.execute("UPDATE shifts SET status = '接班中' WHERE id = ?", (to_shift_id,))
        conn.commit()
    return get_handover(hid)


def _snapshot_violations(conn, handover: dict, items: list[dict]) -> list[dict]:
    """仅按发起时固化快照判断，绝不重读当前资格矩阵。"""
    rows = conn.execute(
        """
        SELECT side, zone_id, qualified
        FROM handover_qualification_snapshots
        WHERE handover_id = ?
        """,
        (handover["id"],),
    ).fetchall()
    if not rows:
        return [{"原因": "交班单缺少发起时的资格快照"}]
    snap = {(r["side"], r["zone_id"]): r["qualified"] for r in rows}
    violations = []
    seen: set[int] = set()
    for it in items:
        zid = it["zone_id"]
        if zid in seen:
            continue
        seen.add(zid)
        for side in ("from", "to"):
            q = snap.get((side, zid))
            if q != 1:
                violations.append(
                    {
                        "原因": "资格快照为否",
                        "双方": SIDE_LABEL[side],
                        "shift_id": handover[f"{side}_shift_id"],
                        "zone_id": zid,
                        "zone_name": it.get("zone_name"),
                        "资格": _qualification_label(q),
                    }
                )
    return violations


def complete_handover(handover_id: int) -> dict:
    with connect() as conn:
        h = conn.execute("SELECT * FROM handovers WHERE id = ?", (handover_id,)).fetchone()
        if not h:
            raise HTTPException(404, "交班单不存在")
        if h["status"] != "进行中":
            raise HTTPException(400, "只能完成进行中的交班单")
        hd = dict(h)
        items = rows_to_dicts(
            conn.execute(
                """
                SELECT hi.batch_id, b.zone_id, z.code AS zone_code, z.name AS zone_name
                FROM handover_items hi
                JOIN batches b ON b.id = hi.batch_id
                LEFT JOIN zones z ON z.id = b.zone_id
                WHERE hi.handover_id = ?
                ORDER BY b.id
                """,
                (handover_id,),
            ).fetchall()
        )
        before = len(items)

        # 完成闸门只看固化快照；当前矩阵变严/变宽都不影响判定。
        violations = _snapshot_violations(conn, hd, items)
        if violations:
            reason = "资格快照不通过，禁止过户：" + "；".join(
                f"{v['双方']}对责任区「{v.get('zone_name') or v.get('zone_id')}」资格={v.get('资格', '否')}"
                for v in violations
            )
            detail = {
                "阶段": "资格快照校验",
                "原因": reason,
                "冲突": violations,
                "交班前在途数": before,
                "交班后交班方在途": conn.execute(
                    "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
                    (hd["from_shift_id"],),
                ).fetchone()["c"],
                "交班后接班方在途": conn.execute(
                    "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
                    (hd["to_shift_id"],),
                ).fetchone()["c"],
            }
            conn.execute(
                """
                UPDATE handovers
                SET status = '失败', finished_at = ?, reason = ?
                WHERE id = ?
                """,
                (_now(), reason, handover_id),
            )
            conn.execute("UPDATE shifts SET status = '值班中' WHERE id = ?", (hd["from_shift_id"],))
            conn.execute("UPDATE shifts SET status = '空闲' WHERE id = ?", (hd["to_shift_id"],))
            conn.execute(
                """
                INSERT INTO reconcile_reports(
                  handover_id, before_count, after_from_count, after_to_count,
                  consistent, detail_json, created_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    handover_id,
                    before,
                    detail["交班后交班方在途"],
                    detail["交班后接班方在途"],
                    json.dumps(detail, ensure_ascii=False),
                    _now(),
                ),
            )
            conn.commit()
            return get_handover(handover_id)

        # 交班完成：整批过户，禁止只改交班单状态不改归属
        for it in items:
            conn.execute(
                "UPDATE batches SET owner_shift_id = ? WHERE id = ? AND status = '在途'",
                (hd["to_shift_id"], it["batch_id"]),
            )
        after_from = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
            (hd["from_shift_id"],),
        ).fetchone()["c"]
        after_to = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
            (hd["to_shift_id"],),
        ).fetchone()["c"]
        consistent = 1 if after_from == 0 and after_to >= before else 0
        detail = {
            "交班前在途数": before,
            "交班后交班方在途": after_from,
            "交班后接班方在途": after_to,
            "资格快照校验": "通过",
        }
        conn.execute(
            """
            UPDATE handovers
            SET status = ?, finished_at = ?, batch_count = ?
            WHERE id = ?
            """,
            ("已完成" if consistent else "失败", _now(), before, handover_id),
        )
        if consistent:
            conn.execute("UPDATE shifts SET status = '已交班' WHERE id = ?", (hd["from_shift_id"],))
            conn.execute("UPDATE shifts SET status = '值班中' WHERE id = ?", (hd["to_shift_id"],))
        else:
            conn.execute(
                "UPDATE handovers SET reason = ? WHERE id = ?",
                ("交班后批次归属与清单不一致", handover_id),
            )
            conn.execute("UPDATE shifts SET status = '值班中' WHERE id = ?", (hd["from_shift_id"],))
            conn.execute("UPDATE shifts SET status = '空闲' WHERE id = ?", (hd["to_shift_id"],))
        conn.execute(
            """
            INSERT INTO reconcile_reports(
              handover_id, before_count, after_from_count, after_to_count,
              consistent, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handover_id,
                before,
                after_from,
                after_to,
                consistent,
                json.dumps(detail, ensure_ascii=False),
                _now(),
            ),
        )
        conn.commit()
    return get_handover(handover_id)


def list_reconcile() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reconcile_reports ORDER BY id DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d.pop("detail_json"))
            d["一致"] = bool(d.pop("consistent"))
            out.append(d)
        return out


# ---------------------------------------------------------------- 批次结清 / 改归属

def _batch_with_zone(conn, batch_id: int) -> dict:
    row = conn.execute(
        """
        SELECT b.*, z.code AS zone_code, z.name AS zone_name
        FROM batches b
        LEFT JOIN zones z ON z.id = b.zone_id
        WHERE b.id = ?
        """,
        (batch_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "批次不存在")
    return dict(row)


def _shift_name(conn, shift_id: int) -> str:
    row = conn.execute("SELECT name FROM shifts WHERE id = ?", (shift_id,)).fetchone()
    return row["name"] if row else f"#{shift_id}"


def _qualification_problem(
    matrix: dict[tuple[int, int], int], shift_id: int, shift_name: str, zone: dict
) -> dict | None:
    q = matrix.get((shift_id, zone["zone_id"]), 0) if zone["zone_id"] is not None else 0
    if q == 1:
        return None
    return {
        "shift_id": shift_id,
        "shift_name": shift_name,
        "zone_id": zone["zone_id"],
        "zone_code": zone["zone_code"],
        "zone_name": zone["zone_name"],
        "qualified": False,
        "资格": "否",
    }


def settle_batch(batch_id: int, shift_id: int) -> dict:
    """结清：归属班次必须对该批次责任区具备资格，否则拒绝并写明资格。"""
    with connect() as conn:
        batch = _batch_with_zone(conn, batch_id)
        if batch["status"] != "在途":
            raise HTTPException(400, "只能结清在途批次")
        if batch["owner_shift_id"] != shift_id:
            raise HTTPException(409, "只有批次归属班次可以结清")
        if batch["zone_id"] is None:
            raise HTTPException(
                409,
                f"资格冲突：批次 {batch['biz_key']} 未标责任区，无法确认班次资格，禁止结清",
            )
        zone = {
            "zone_id": batch["zone_id"],
            "zone_code": batch["zone_code"],
            "zone_name": batch["zone_name"],
        }
        matrix = _matrix_map(conn)
        name = _shift_name(conn, shift_id)
        problem = _qualification_problem(matrix, shift_id, name, zone)
        if problem:
            raise HTTPException(
                409,
                f"资格冲突：班次「{name}」对责任区「{batch['zone_name']}」资格=否，"
                f"禁止结清批次 {batch['biz_key']}",
            )
        conn.execute("UPDATE batches SET status = '已结清' WHERE id = ?", (batch_id,))
        conn.commit()
        batch["status"] = "已结清"
        return batch


def reassign_batch(batch_id: int, to_shift_id: int) -> dict:
    """改归属：原归属班次与目标班次都必须对该批次责任区具备资格。"""
    with connect() as conn:
        batch = _batch_with_zone(conn, batch_id)
        if batch["status"] != "在途":
            raise HTTPException(400, "只能改归属在途批次")
        target = conn.execute("SELECT * FROM shifts WHERE id = ?", (to_shift_id,)).fetchone()
        if not target:
            raise HTTPException(404, "目标班次不存在")
        if batch["owner_shift_id"] == to_shift_id:
            raise HTTPException(400, "批次已归属该班次")
        if batch["zone_id"] is None:
            raise HTTPException(
                409,
                f"资格冲突：批次 {batch['biz_key']} 未标责任区，无法确认班次资格，禁止改归属",
            )
        zone = {
            "zone_id": batch["zone_id"],
            "zone_code": batch["zone_code"],
            "zone_name": batch["zone_name"],
        }
        matrix = _matrix_map(conn)
        problems = []
        from_name = _shift_name(conn, batch["owner_shift_id"]) if batch["owner_shift_id"] else "—"
        if batch["owner_shift_id"] is not None:
            p = _qualification_problem(matrix, batch["owner_shift_id"], from_name, zone)
            if p:
                problems.append(p)
        p = _qualification_problem(matrix, to_shift_id, target["name"], zone)
        if p:
            problems.append(p)
        if problems:
            parts = [
                f"班次「{p['shift_name']}」对责任区「{p['zone_name'] or p['zone_id']}」资格=否"
                for p in problems
            ]
            raise HTTPException(
                409,
                "资格冲突，禁止改归属批次 "
                f"{batch['biz_key']}：" + "；".join(parts),
            )
        conn.execute(
            "UPDATE batches SET owner_shift_id = ? WHERE id = ?",
            (to_shift_id, batch_id),
        )
        conn.commit()
        return _batch_with_zone(conn, batch_id)
