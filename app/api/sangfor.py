from fastapi import APIRouter, Depends, Query

from app.middleware.auth import get_current_user
from app.schemas.common import success
from app.schemas.sangfor import SangforCreate, SangforResubmit
from app.services.sangfor import SangforService

router = APIRouter(prefix="/sangfor", tags=["深信服认证"])
service = SangforService()


@router.get("/batches")
async def batches(product_code: str | None = Query(None, max_length=32)):
    return success(data=await service.batches(public=True, product_code=product_code))


@router.get("/batches/{batch_id}")
async def batch(batch_id: int):
    return success(data=await service.get_batch(batch_id, public=True))


@router.post("/registrations")
async def create(body: SangforCreate, user=Depends(get_current_user)):
    return success(data=await service.create(user.id, body))


@router.get("/registrations")
async def registrations(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), user=Depends(get_current_user)):
    return success(data=await service.registrations(user_id=user.id, page=page, page_size=page_size))


@router.get("/registrations/{registration_id}")
async def detail(registration_id: int, user=Depends(get_current_user)):
    return success(data=await service.detail(registration_id, user.id))


@router.post("/registrations/{registration_id}/resubmit")
async def resubmit(registration_id: int, body: SangforResubmit, user=Depends(get_current_user)):
    return success(data=await service.resubmit(registration_id, user.id, body))
