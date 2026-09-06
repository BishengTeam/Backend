import csv
import io
import secrets

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import Certification
from app.models.cert_product import CertProduct
from app.port.exceptions import NotFoundException

from app.schemas.certification import (
    CertificationDetailResponse,
    CertificationFilter,
    CertificationResponse,
    SangforCouponResponse,
)


class CertificationService:

    async def list_certifications(self, filters: CertificationFilter | None = None) -> list[CertificationResponse]:
        """优先返回新 cert_product（活跃），兼容旧 certification 补充"""
        async with get_db_ctx() as db:
            seen_codes: set[str] = set()
            result_items: list[CertificationResponse] = []
            # 新表（按 type 筛选映射 vendor）
            vendor_map = {"h3c": "H3C", "renshe": "人社", "sangfor": "深信服", "nisp": "NISP"}
            new_stmt = select(CertProduct).where(CertProduct.is_active == True)
            if filters and filters.vendor:
                type_filter = {v: k for k, v in vendor_map.items()}.get(filters.vendor)
                if type_filter:
                    new_stmt = new_stmt.where(CertProduct.type == type_filter)
            new_certs = (await db.execute(new_stmt.order_by(CertProduct.type, CertProduct.id))).scalars().all()
            for cp in new_certs:
                seen_codes.add(cp.code)
                # 构造与旧 CertificationResponse 兼容的 shape
                result_items.append(CertificationResponse(
                    id=cp.id,
                    name=cp.name,
                    chinese_name=cp.chinese_name,
                    code=cp.code,
                    vendor=vendor_map.get(cp.type, cp.type),
                    requires_xuexin=False,
                    pay_first=True,
                ))
            # 旧表补充（新表未覆盖的 code）
            old_stmt = select(Certification).where(
                Certification.is_active == True,
                ~Certification.code.in_(seen_codes) if seen_codes else True,
            )
            if filters and filters.vendor:
                old_stmt = old_stmt.where(Certification.vendor == filters.vendor)
            old_certs = (await db.execute(old_stmt.order_by(Certification.id))).scalars().all()
            for c in old_certs:
                result_items.append(CertificationResponse.model_validate(c))
            return result_items

    async def get_detail(self, cert_id: int) -> CertificationDetailResponse:
        async with get_db_ctx() as db:
            # 优先新表
            cp = await db.get(CertProduct, cert_id)
            if cp is not None:
                vendor_map = {"h3c": "H3C", "renshe": "人社", "sangfor": "深信服", "nisp": "NISP"}
                return CertificationDetailResponse(
                    id=cp.id, name=cp.name, chinese_name=cp.chinese_name,
                    code=cp.code, vendor=vendor_map.get(cp.type, cp.type),
                    requires_xuexin=False, pay_first=True,
                    description=cp.description, price_normal=None, price_student=None,
                )
            cert = await db.get(Certification, cert_id)
            if cert is None:
                raise NotFoundException("认证项目")
            return CertificationDetailResponse.model_validate(cert)

    # ── P2 深信服/NISP ──────────────────────────────────────────

    async def get_sangfor_coupons(self) -> list[SangforCouponResponse]:
        """查询深信服考试券：vendor='深信服' 且 is_active=true"""
        async with get_db_ctx() as db:
            stmt = (
                select(Certification)
                .where(Certification.vendor == "深信服", Certification.is_active == True)
                .order_by(Certification.id)
            )
            result = await db.execute(stmt)
            certs = result.scalars().all()
            return [SangforCouponResponse.model_validate(c) for c in certs]

    @staticmethod
    def generate_verify_code() -> str:
        """生成 6 位十六进制验证码"""
        return secrets.token_hex(3)

    @staticmethod
    def convert_to_pinyin(name: str) -> str:
        """拼音转换；pypinyin 不可用时返回 mock 拼音"""
        try:
            from pypinyin import lazy_pinyin  # type: ignore[import-untyped]
        except ImportError:
            return f"mock_{name}"
        parts = lazy_pinyin(name)
        return " ".join(parts) if parts else f"mock_{name}"

    @staticmethod
    def get_nisp_template() -> dict:
        """NISP 模板（mock 实现）"""
        return {"message": "模板功能开发中", "template_url": None}

    async def export_csv(self) -> str:
        """导出认证为 CSV"""
        async with get_db_ctx() as db:
            result = await db.execute(
                select(Certification).order_by(Certification.id)
            )
            certs = result.scalars().all()

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["ID", "认证名称", "厂商", "中文名称", "code", "是否需要学信网", "是否先支付", "是否激活"])
            for c in certs:
                writer.writerow([
                    c.id,
                    c.name,
                    c.vendor,
                    c.chinese_name,
                    c.code,
                    "是" if c.requires_xuexin else "否",
                    "是" if c.pay_first else "否",
                    "是" if c.is_active else "否",
                ])

            return output.getvalue()