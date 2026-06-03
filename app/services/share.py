import secrets

from sqlalchemy import select

from app.core.database import get_db_ctx
from app.core.exceptions import NotFoundException
from app.models.share import Share
from app.schemas.share import ShareCreateRequest, ShareCreateResponse, ShareResponse


class ShareService:

    async def create_share(self, user_id: int, data: ShareCreateRequest) -> ShareCreateResponse:
        async with get_db_ctx() as db:
            code = secrets.token_hex(4)
            share = Share(
                user_id=user_id,
                code=code,
                target_type=data.target_type,
                target_id=data.target_id,
            )
            db.add(share)
            await db.commit()
            await db.refresh(share)
            return ShareCreateResponse(
                code=share.code,
                share_url=f"/api/share/{share.code}",
            )

    async def track_visit(self, code: str) -> ShareResponse:
        async with get_db_ctx() as db:
            result = await db.execute(select(Share).where(Share.code == code))
            share = result.scalar_one_or_none()
            if share is None:
                raise NotFoundException("分享记录")
            # atomically increment visit_count via UPDATE ... RETURNING equivalent
            share.visit_count += 1
            await db.commit()
            await db.refresh(share)
            return ShareResponse.model_validate(share)
