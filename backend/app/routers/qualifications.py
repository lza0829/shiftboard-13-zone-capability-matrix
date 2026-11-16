from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import handover as svc

router = APIRouter(tags=["资格矩阵"])


class QualificationBody(BaseModel):
    shift_id: int = Field(..., description="班次")
    zone_id: int = Field(..., description="责任区")
    qualified: bool = Field(..., description="是否具备资格")


@router.get("/zones")
def get_zones():
    return svc.list_zones()


@router.get("/qualifications")
def get_qualifications():
    return svc.get_qualifications()


@router.put("/qualifications")
def put_qualification(body: QualificationBody):
    return svc.set_qualification(body.shift_id, body.zone_id, body.qualified)


@router.get("/qualification-audit")
def get_audit():
    """矩阵、交班单快照、发起失败原因同源读取。"""
    return {
        "矩阵": svc.get_qualifications(),
        "快照": svc.list_snapshots(),
        "发起拒绝": svc.list_start_rejections(),
    }


@router.get("/qualification-snapshots")
def get_snapshots(handover_id: int | None = None):
    return svc.list_snapshots(handover_id)


@router.get("/handover-rejections")
def get_rejections():
    return svc.list_start_rejections()
