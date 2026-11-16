from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.services import handover as svc

router = APIRouter(prefix="/batches", tags=["在途批次"])


class SettleBody(BaseModel):
    shift_id: int = Field(..., description="结清操作班次")


class ReassignBody(BaseModel):
    to_shift_id: int = Field(..., description="目标归属班次")


@router.get("")
def get_batches(owner_shift_id: int | None = Query(default=None)):
    return svc.list_batches(owner_shift_id)


@router.post("/{batch_id}/settle")
def settle(batch_id: int, body: SettleBody):
    return svc.settle_batch(batch_id, body.shift_id)


@router.post("/{batch_id}/reassign")
def reassign(batch_id: int, body: ReassignBody):
    return svc.reassign_batch(batch_id, body.to_shift_id)
