from sqlalchemy import Integer as SAInteger, func, select

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order, PriceConfig
from app.domain.plan.src.index import Plan
from app.models.cert_product import CertProduct
from app.models.cert_product_catalog import CertProductCatalog
from app.port.exceptions import BusinessException, NotFoundException
from app.schemas.admin_cert_product import (
    CertProductCreate,
    CertProductPrice,
    CertProductResponse,
    CertProductCatalogResponse,
    CertProductStats,
    CertProductUpdate,
)
from app.schemas.common import PaginatedData

TYPE_LABELS: dict[str, str] = {
    "h3c": "H3C 认证",
    "renshe": "人社认证",
}

PRICE_USER_TYPES = ("student", "normal")


class AdminCertProductService:
    async def list_catalog(
        self, type: str | None
    ) -> list[CertProductCatalogResponse]:
        """列出产品目录，标记是否已创建为产品（供选择框过滤）"""
        async with get_db_ctx() as db:
            stmt = select(CertProductCatalog).order_by(CertProductCatalog.code.asc())
            if type:
                stmt = stmt.where(CertProductCatalog.type == type)
            rows = (await db.execute(stmt)).scalars().all()
            used_codes = {
                row[0] for row in (await db.execute(select(CertProduct.code))).all()
            }
            return [
                CertProductCatalogResponse.model_validate(
                    row
                ).model_copy(update={"instantiated": row.code in used_codes})
                for row in rows
            ]

    async def list_products(
        self,
        type: str | None,
        keyword: str | None,
        page: int,
        page_size: int,
    ) -> PaginatedData[CertProductResponse]:
        """按 type 筛选，支持 keyword 搜索 code/name/chinese_name"""
        async with get_db_ctx() as db:
            base = select(CertProduct)
            if type:
                base = base.where(CertProduct.type == type)
            if keyword:
                pattern = f"%{keyword}%"
                base = base.where(
                    CertProduct.code.ilike(pattern)
                    | CertProduct.name.ilike(pattern)
                    | CertProduct.chinese_name.ilike(pattern)
                )
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = (
                base.order_by(CertProduct.sort_order.asc(), CertProduct.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            result = await db.execute(stmt)
            products = result.scalars().all()
            prices_by_code = await self._load_active_prices(
                db, [product.code for product in products]
            )
            items = [
                CertProductResponse.model_validate(
                    product
                ).model_copy(update={"prices": prices_by_code.get(product.code, [])})
                for product in products
            ]
            return PaginatedData[CertProductResponse](
                items=items,
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create(self, data: CertProductCreate) -> CertProductResponse:
        """创建认证产品"""
        async with get_db_ctx() as db:
            async with db.begin():
                if data.catalog_id is not None:
                    catalog = await db.get(CertProductCatalog, data.catalog_id)
                    if catalog is None:
                        raise NotFoundException("认证产品目录")
                    if catalog.type != data.type:
                        raise BusinessException("目录项与认证类型不匹配")
                    if data.code != catalog.code:
                        raise BusinessException("产品编码必须与目录项一致")
                product = CertProduct(
                    type=data.type,
                    catalog_id=data.catalog_id,
                    code=data.code,
                    name=data.name,
                    chinese_name=data.chinese_name,
                    description=data.description,
                    is_active=data.is_active,
                    sort_order=data.sort_order,
                )
                db.add(product)
                await db.flush()
                await self._sync_prices(db, data.code, data.prices)
                await db.refresh(product)
            prices = await self._load_active_prices(db, [product.code])
            return CertProductResponse.model_validate(
                product
            ).model_copy(update={"prices": prices.get(product.code, [])})

    async def get_by_code(self, code: str) -> CertProductResponse:
        """按 code 获取单个认证产品"""
        async with get_db_ctx() as db:
            product = await self._get_or_404(db, code)
            prices = await self._load_active_prices(db, [product.code])
            return CertProductResponse.model_validate(
                product
            ).model_copy(update={"prices": prices.get(product.code, [])})

    async def update(self, code: str, data: CertProductUpdate) -> CertProductResponse:
        """按 code 更新认证产品"""
        async with get_db_ctx() as db:
            async with db.begin():
                product = await self._get_or_404(db, code)
                update_data = data.model_dump(exclude_unset=True)
                update_data.pop("prices", None)
                for field, value in update_data.items():
                    setattr(product, field, value)
                await self._sync_prices(db, product.code, data.prices)
                await db.flush()
                await db.refresh(product)
            prices = await self._load_active_prices(db, [product.code])
            return CertProductResponse.model_validate(
                product
            ).model_copy(update={"prices": prices.get(product.code, [])})

    async def deactivate(self, code: str) -> None:
        """软删除（设 is_active=False）"""
        async with get_db_ctx() as db:
            product = await self._get_or_404(db, code)
            product.is_active = False
            await db.commit()

    async def get_stats(self) -> list[CertProductStats]:
        """聚合统计，按 type 分组"""
        async with get_db_ctx() as db:
            # 按 type 聚合产品统计
            product_agg = (
                select(
                    CertProduct.type,
                    func.count().label("product_count"),
                    func.sum(func.cast(CertProduct.is_active, SAInteger)).label("active_product_count"),
                )
                .group_by(CertProduct.type)
                .subquery()
            )

            # 活跃批次数（plan status='published'）按 product_type 分组
            batch_agg = (
                select(
                    Plan.product_type,
                    func.count().label("active_batch_count"),
                )
                .where(Plan.status == "published")
                .group_by(Plan.product_type)
                .subquery()
            )

            # 报名总数（order status in paid/completed）按 product_type 分组
            enrolled_agg = (
                select(
                    Order.product_type,
                    func.count().label("total_enrolled"),
                )
                .where(Order.status.in_(["paid", "completed"]))
                .group_by(Order.product_type)
                .subquery()
            )

            stmt = (
                select(
                    product_agg.c.type,
                    product_agg.c.product_count,
                    product_agg.c.active_product_count,
                    func.coalesce(batch_agg.c.active_batch_count, 0).label("active_batch_count"),
                    func.coalesce(enrolled_agg.c.total_enrolled, 0).label("total_enrolled"),
                )
                .outerjoin(batch_agg, product_agg.c.type == batch_agg.c.product_type)
                .outerjoin(enrolled_agg, product_agg.c.type == enrolled_agg.c.product_type)
            )

            result = await db.execute(stmt)
            rows = result.all()

            return [
                CertProductStats(
                    type=row.type,
                    type_label=TYPE_LABELS.get(row.type, row.type),
                    product_count=row.product_count,
                    active_product_count=row.active_product_count or 0,
                    active_batch_count=row.active_batch_count,
                    total_enrolled=row.total_enrolled,
                )
                for row in rows
            ]

    @staticmethod
    async def _get_or_404(db, code: str) -> CertProduct:
        product = (
            await db.execute(
                select(CertProduct).where(CertProduct.code == code)
            )
        ).scalar_one_or_none()
        if product is None:
            raise NotFoundException("认证产品")
        return product

    @staticmethod
    async def _load_active_prices(
        db, codes: list[str]
    ) -> dict[str, list[CertProductPrice]]:
        """按产品编码批量加载生效价格，返回 {code: [price, ...]}"""
        if not codes:
            return {}
        rows = (
            await db.execute(
                select(PriceConfig).where(
                    PriceConfig.product_type.in_(codes),
                    PriceConfig.is_active.is_(True),
                    PriceConfig.user_type.in_(PRICE_USER_TYPES),
                )
            )
        ).scalars().all()
        by_code: dict[str, list[CertProductPrice]] = {}
        for row in rows:
            by_code.setdefault(row.product_type, []).append(
                CertProductPrice(
                    user_type=row.user_type,
                    price_cents=row.price,
                )
            )
        return by_code

    @staticmethod
    async def _sync_prices(
        db, code: str, prices: list[CertProductPrice] | None
    ) -> None:
        """将产品价格同步到 price_config。

        prices 为 None 表示调用方未传、保持不动；
        否则 student/normal 档位以传入列表为准（新增/更新/停用多余档位），
        空列表会停用该产品全部标准档位。操作幂等。
        """
        if prices is None:
            return
        wanted = {item.user_type: item.price_cents for item in prices}
        rows = (
            await db.execute(
                select(PriceConfig).where(
                    PriceConfig.product_type == code,
                    PriceConfig.user_type.in_(PRICE_USER_TYPES),
                )
            )
        ).scalars().all()
        for row in rows:
            if row.user_type in wanted:
                row.price = wanted.pop(row.user_type)
                row.is_active = True
            else:
                row.is_active = False
        for user_type, price_cents in wanted.items():
            db.add(
                PriceConfig(
                    product_type=code,
                    user_type=user_type,
                    price=price_cents,
                    is_active=True,
                )
            )
