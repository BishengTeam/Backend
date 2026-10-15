import secrets

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.domain.community.src.index import Share
from app.domain.content.src.index import SharePoster
from app.schemas.share import ShareCreateRequest, ShareCreateResponse, SharePosterResponse, ShareResponse


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

    async def get_active_poster(self) -> SharePosterResponse:
        """分享好友页海报：取最新一条启用中的分享海报，未配置返回 null"""
        async with get_db_ctx() as db:
            poster = (
                await db.execute(
                    select(SharePoster)
                    .where(SharePoster.is_active == True)  # noqa: E712
                    .order_by(SharePoster.id.desc())
                    .limit(1)
                )
            ).scalars().first()
            return SharePosterResponse(image_url=poster.image_url if poster else None)