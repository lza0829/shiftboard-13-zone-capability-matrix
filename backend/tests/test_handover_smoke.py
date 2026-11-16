from pathlib import Path
import os
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["DATA_DIR"] = str(ROOT / "backend" / "data_test")
os.environ["FIXTURES_DIR"] = str(ROOT / "fixtures")

db_path = Path(os.environ["DATA_DIR"]) / "app.db"
if db_path.exists():
    db_path.unlink()

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["项目"] == "交班台"


def test_matrix_is_complete_and_seeded(client):
    data = client.get("/api/qualifications").json()
    assert data["责任区"]
    assert len(data["矩阵"]) == len(data["班次"]) * len(data["责任区"])
    cells = {(c["shift_id"], c["zone_id"]): c["qualified"] for c in data["矩阵"]}
    assert cells[(1, 2)] is True
    assert cells[(2, 2)] is False


def test_start_rejected_when_receiver_lacks_a_zone(client):
    # 早班在途批次覆盖 常温区(1) 与 冷藏区(2)；夜班只有冷藏区资格 → 整单拒绝
    r = client.post("/api/handovers/start", json={"from_shift_id": 1, "to_shift_id": 3})
    assert r.status_code == 409, r.text
    reason = r.json()["detail"]
    assert "夜班" in reason and "常温区" in reason

    rejects = client.get("/api/handover-rejections").json()
    assert rejects
    detail = rejects[0]["detail"]
    assert detail["阶段"] == "资格校验"
    assert {c["zone_id"] for c in detail["冲突"]} == {1}
    assert {c["side"] for c in detail["冲突"]} == {"to"}
    assert not any(h["status"] == "进行中" for h in client.get("/api/handovers").json())


def test_matrix_maintainable_and_unlocks_start(client):
    # 补授夜班常温区资格后，同一起交班应可创建
    r = client.put(
        "/api/qualifications",
        json={"shift_id": 3, "zone_id": 1, "qualified": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["qualified"] is True

    started = client.post("/api/handovers/start", json={"from_shift_id": 1, "to_shift_id": 3})
    assert started.status_code == 200, started.text
    hid = started.json()["id"]
    assert started.json()["status"] == "进行中"

    snap = started.json()["资格快照"]
    got = {(s["side"], s["zone_id"]): s["qualified"] for s in snap}
    assert got[("from", 1)] is True and got[("from", 2)] is True
    assert got[("to", 1)] is True and got[("to", 2)] is True

    # 并行交班仍应被拒绝（且拒绝原因留档）
    again = client.post("/api/handovers/start", json={"from_shift_id": 1, "to_shift_id": 2})
    assert again.status_code == 409
    assert client.get("/api/handover-rejections").json()[0]["detail"]["阶段"] == "互斥校验"

    # 完成阶段不得因当前矩阵变严而拒单：撤回夜班常温区资格后仍应完成
    client.put("/api/qualifications", json={"shift_id": 3, "zone_id": 1, "qualified": False})
    done = client.post(f"/api/handovers/{hid}/complete")
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "已完成"

    to_batches = client.get("/api/batches", params={"owner_shift_id": 3}).json()
    assert sum(1 for b in to_batches if b["status"] == "在途") >= 4


def test_snapshot_is_immutable(client):
    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE handover_qualification_snapshots SET qualified = 0")
        conn.rollback()
    finally:
        conn.close()


def test_complete_gates_on_frozen_snapshot_only(client):
    """快照为否的单即便当前矩阵已变宽，也不得过户。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from app.services.handover import _now

        # 早班仍有 4 个在途批次（上一单已过户给夜班，这里取夜班批次构造反向单）
        batch = conn.execute("SELECT id, zone_id FROM batches WHERE status='在途' ORDER BY id").fetchone()
        cur = conn.execute(
            """
            INSERT INTO handovers(from_shift_id, to_shift_id, status, batch_count, started_at)
            VALUES (3, 2, '进行中', 1, ?)
            """,
            (_now(),),
        )
        hid = cur.lastrowid
        conn.execute(
            "INSERT INTO handover_items(handover_id, batch_id) VALUES (?, ?)",
            (hid, batch["id"]),
        )
        # 故意写入“发起时不合格”的快照：接班方对清单责任区=否
        conn.execute(
            """
            INSERT INTO handover_qualification_snapshots(
              handover_id, shift_id, side, zone_id, qualified, captured_at
            ) VALUES (?, 3, 'from', ?, 1, ?)
            """,
            (hid, batch["zone_id"], _now()),
        )
        conn.execute(
            """
            INSERT INTO handover_qualification_snapshots(
              handover_id, shift_id, side, zone_id, qualified, captured_at
            ) VALUES (?, 2, 'to', ?, 0, ?)
            """,
            (hid, batch["zone_id"], _now()),
        )
        conn.commit()
    finally:
        conn.close()

    # 当前矩阵变宽：若完成时重读矩阵就会放过，但只解释快照 → 必须失败且不过户
    client.put("/api/qualifications", json={"shift_id": 2, "zone_id": batch["zone_id"], "qualified": True})
    done = client.post(f"/api/handovers/{hid}/complete")
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "失败"
    assert "资格快照不通过" in done.json()["reason"]

    after = client.get("/api/batches").json()
    got = {b["id"]: b for b in after}[batch["id"]]
    assert got["owner_shift_id"] == 3


def test_settle_and_reassign_enforce_qualification(client):
    # 此时夜班持有批次 1~4；常温区批次 2，夜班常温区资格已被撤回
    r = client.post("/api/batches/2/settle", json={"shift_id": 3})
    assert r.status_code == 409
    assert "常温区" in r.json()["detail"]

    # 改归属：原归属夜班无常温区资格 → 即便目标晚班有资格也整单拒绝，并写明双方
    r = client.post("/api/batches/2/reassign", json={"to_shift_id": 2})
    assert r.status_code == 409
    assert "夜班" in r.json()["detail"] and "常温区" in r.json()["detail"]

    # 补授夜班常温区资格后，改归属与结清均可执行
    client.put("/api/qualifications", json={"shift_id": 3, "zone_id": 1, "qualified": True})
    r = client.post("/api/batches/2/reassign", json={"to_shift_id": 2})
    assert r.status_code == 200, r.text
    assert r.json()["owner_shift_id"] == 2

    r = client.post("/api/batches/2/settle", json={"shift_id": 2})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "已结清"


def test_audit_sources_are_shared(client):
    audit = client.get("/api/qualification-audit").json()
    assert audit["矩阵"]["矩阵"]
    assert audit["快照"]
    assert audit["发起拒绝"]
    # 快照里的班次/责任区标识必须能在矩阵中找到同名条目
    matrix_cells = {
        (c["shift_id"], c["zone_id"]) for c in audit["矩阵"]["矩阵"]
    }
    assert all((s["shift_id"], s["zone_id"]) in matrix_cells for s in audit["快照"])
