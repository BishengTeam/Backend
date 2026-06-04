import csv
import io
import secrets

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import Certification
from app.port.exceptions import NotFoundException

from app.schemas.certification import (
    CertificationDetailResponse,
    CertificationFilter,
    CertificationResponse,
    SangforCouponResponse,
)


class CertificationService:

    async def list_certifications(self, filters: CertificationFilter | None = None) -> list[CertificationResponse]:
        async with get_db_ctx() as db:
            stmt = select(Certification).where(Certification.is_active == True)
            if filters and filters.vendor:
                stmt = stmt.where(Certification.vendor == filters.vendor)
            result = await db.execute(stmt.order_by(Certification.id))
            certs = result.scalars().all()
            return [CertificationResponse.model_validate(c) for c in certs]

    async def get_detail(self, cert_id: int) -> CertificationDetailResponse:
        async with get_db_ctx() as db:
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