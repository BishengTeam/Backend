from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.domain.content.src.index import GzhArticle
from app.domain.content.src.model.gzh_article import SOURCE_MANUAL
from app.port.exceptions import ConflictException, NotFoundException
from app.schemas.admin_gzh_article import (
    GzhArticleBatchImportRequest,
    GzhArticleBatchImportResult,
    GzhArticleCreate,
    GzhArticleListItem,
    GzhArticleUpdate,
)
from app.schemas.common import PaginatedData
from app.services.recycle_bin import restore_row, soft_delete_row


class AdminGzhArticleService:

    async def list_articles(
        self,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[GzhArticleListItem]:
        async with get_db_ctx() as db:
            base = select(GzhArticle).where(GzhArticle.deleted_at.is_(None))
            if keyword:
                base = base.where(GzhArticle.title.ilike(f"%{keyword}%"))
            total = (
                await db.execute(select(func.count()).select_from(base.subquery()))
            ).scalar() or 0
            rows = (
                await db.execute(
                    base.order_by(GzhArticle.publish_time.desc().nullslast(), GzhArticle.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData(
                items=[GzhArticleListItem.model_validate(r) for r in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create(self, data: GzhArticleCreate) -> GzhArticleListItem:
        async with get_db_ctx() as db:
            existing = await self._find_by_url(db, data.url)
            if existing is not None:
                if existing.deleted_at is None:
                    raise ConflictException("该文章链接已存在")
                return await self._revive(db, existing, data.model_dump())
            article = GzhArticle(source=SOURCE_MANUAL, **data.model_dump())
            db.add(article)
            await db.commit()
            await db.refresh(article)
            return GzhArticleListItem.model_validate(article)

    async def update(self, article_id: int, data: GzhArticleUpdate) -> GzhArticleListItem:
        async with get_db_ctx() as db:
            article = await db.get(GzhArticle, article_id)
            if article is None or article.deleted_at is not None:
                raise NotFoundException("公众号资讯")
            update_data = data.model_dump(exclude_unset=True)
            new_url = update_data.get("url")
            if new_url and new_url != article.url:
                other = await self._find_by_url(db, new_url)
                if other is not None and other.id != article.id:
                    raise ConflictException("该文章链接已存在")
            for key, value in update_data.items():
                setattr(article, key, value)
            # 经 Admin 保存过的条目转为手动维护，不再被同步 worker 覆盖/清理
            article.source = SOURCE_MANUAL
            await db.commit()
            await db.refresh(article)
            return GzhArticleListItem.model_validate(article)

    async def delete(self, article_id: int) -> None:
        """软删除进回收站，30 自然日内可恢复。"""
        async with get_db_ctx() as db:
            article = await db.get(GzhArticle, article_id)
            if article is None or article.deleted_at is not None:
                raise NotFoundException("公众号资讯")
            soft_delete_row(article)
            await db.commit()

    async def batch_delete(self, article_ids: list[int]) -> int:
        async with get_db_ctx() as db:
            result = await db.execute(
                select(GzhArticle).where(
                    GzhArticle.id.in_(article_ids), GzhArticle.deleted_at.is_(None)
                )
            )
            articles = result.scalars().all()
            for article in articles:
                soft_delete_row(article)
            await db.commit()
            return len(articles)

    async def restore(self, article_id: int) -> None:
        """从回收站恢复。"""
        async with get_db_ctx() as db:
            article = await db.get(GzhArticle, article_id)
            if article is None or article.deleted_at is None:
                raise NotFoundException("公众号资讯")
            restore_row(article)
            await db.commit()

    async def batch_import(self, body: GzhArticleBatchImportRequest) -> GzhArticleBatchImportResult:
        """批量导入手动条目；链接已存在（含本轮重复）跳过，软删行复活并覆盖字段。"""
        created = 0
        skipped = 0
        async with get_db_ctx() as db:
            seen_urls: set[str] = set()
            for item in body.items:
                if item.url in seen_urls:
                    skipped += 1
                    continue
                seen_urls.add(item.url)
                existing = await self._find_by_url(db, item.url)
                if existing is not None:
                    if existing.deleted_at is None:
                        skipped += 1
                    else:
                        await self._revive(db, existing, item.model_dump())
                        created += 1
                    continue
                db.add(GzhArticle(source=SOURCE_MANUAL, **item.model_dump()))
                created += 1
            await db.commit()
        return GzhArticleBatchImportResult(created=created, skipped=skipped, errors=[])

    async def _find_by_url(self, db: AsyncSession, url: str) -> GzhArticle | None:
        """url 有唯一约束，按链接查行（含软删行）。"""
        return (
            await db.execute(select(GzhArticle).where(GzhArticle.url == url))
        ).scalars().first()

    async def _revive(self, db: AsyncSession, row: GzhArticle, fields: dict) -> GzhArticleListItem:
        """软删行按新内容复活（运营重新导入同链接文章）。"""
        for key, value in fields.items():
            setattr(row, key, value)
        row.source = SOURCE_MANUAL
        restore_row(row)
        await db.commit()
        await db.refresh(row)
        return GzhArticleListItem.model_validate(row)
