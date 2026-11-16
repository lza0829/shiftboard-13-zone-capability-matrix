# 06-shiftboard（交班台）

交班台 — 班次责任与在途批次交接：班次登记 → 在途批次归属 → 交班互斥办理 → 交班前后对账。

**不是** 多源 Feed 归并，**不是** 作业依赖编排，**不是** CDC 变更流，**不是** 质检签发，**不是** 数仓贴源/明细/汇总分层。  
**也不是** OA 考勤排班后台、电商订单、报表 CSV 看板。

## 启动

本地开发：

```bash
# 后端
cd backend
pip install -r requirements.txt
set DATA_DIR=%CD%\data
set FIXTURES_DIR=%CD%\..\fixtures
uvicorn app.main:app --host 127.0.0.1 --port 10600

# 前端
cd frontend
npm install
npm run dev
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://127.0.0.1:5600 |
| 接口 | http://127.0.0.1:10600 |

## 主链

登记班次与值班人 → 在途批次归属当前班次 → 发起交班（全仓同时仅一单进行中）→ 完成交班整批过户 → 交班对账报告与清单/批次同源

## 资格矩阵（批次责任区）

- **责任区**：批次带 `zone_id`；`zones` 表维护责任区编码与名称。
- **资格矩阵唯一存储**：独立表 `shift_qualifications(shift_id, zone_id, qualified)`，
  `qualified=1` 可 / `0` 否；缺行按「否」。启动时补齐全部 班次 × 责任区，矩阵可完整维护。
  界面「资格矩阵」页可逐格切换。
- **发起交班**：取交班方名下全部在途批次，**清单内每个责任区，交班方与接班方都必须具备资格**；
  任一缺口即整单拒绝（不仅校验接班方，也不只看第一区），拒绝原因写入 `handover_start_rejections`。
- **资格快照**：发起成功即把「双方 × 责任区」资格固化到 `handover_qualification_snapshots`
  （按交班单 id 关联，触发器禁止 UPDATE/DELETE）。**完成阶段只解释快照**，
  不重读当前矩阵：矩阵变严不拒单，变宽也不放过发起时不合格的单。
- **结清 / 改归属**：操作班次必须对该批次责任区具备资格；改归属要求原归属与目标班次双方都具备，
  冲突时返回 409 并写明「哪个班次 × 哪个责任区 = 否」。未标责任区的批次同样拒绝。
- **同源可查**：`/api/qualifications`（矩阵）、`/api/qualification-snapshots`（快照）、
  `/api/handover-rejections`（发起失败原因）、`/api/qualification-audit`（三者合一）
  共用同一套表与标识。

## 界面约定

页面文案全部中文；入口：总览 / 班次 / 在途批次 / 交班办理 / 资格矩阵 / 交班记录 / 交班对账。

## 与已有仓差异

| 仓 | 骨架 |
| --- | --- |
| Feedstitch | 多源解析→冲突隔离→水位 |
| Batchgate | 作业依赖→重试→水位 |
| Changeflow | 事件序→缺口→Sink 游标 |
| Checklane | 入检→规则→隔离签发 |
| Tiercraft | 贴源→明细→汇总→层间对账 |
| **本仓** | **班次归属→交班互斥→批次过户→交班对账** |
