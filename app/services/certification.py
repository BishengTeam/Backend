from sqlalchemy import select

from app.core.database import get_db_ctx
from app.models.certification import Certification
from app.schemas.certification import CertificationFilter, CertificationResponse, XuexinGuideResponse
from app.utils.excel import export_csv

CODE_NAME_MAP: dict[str, str] = {
    "H3C-NE": "H3C 网络工程师",
    "SF-CSE": "深信服网络安全工程师",
    "NISP-1": "NISP 一级",
    "RS-ZY": "人社职业技能等级认定",
}


class CertificationService:

    def lookup_name(self, code: str) -> str:
        return CODE_NAME_MAP.get(code, code)

    async def list_certifications(self, filters: CertificationFilter | None = None) -> list[CertificationResponse]:
        async with get_db_ctx() as db:
            stmt = select(Certification).where(Certification.is_active == True)
            if filters and filters.vendor:
                stmt = stmt.where(Certification.vendor == filters.vendor)
            result = await db.execute(stmt.order_by(Certification.id))
            certs = result.scalars().all()
            return [CertificationResponse.model_validate(c) for c in certs]

    async def export_certifications(self) -> tuple[str, bytes]:
        async with get_db_ctx() as db:
            result = await db.execute(
                select(Certification).where(Certification.is_active == True).order_by(Certification.id)
            )
            certs = result.scalars().all()
        headers = ["ID", "名称", "考试代码", "厂商", "企业价格(元)", "学生价格(元)", "需学信网验证", "先支付后填表"]
        rows = [
            [
                c.id,
                c.name,
                c.code,
                c.vendor,
                f"{c.price_enterprise / 100:.2f}",
                f"{c.price_student / 100:.2f}",
                "是" if c.requires_xuexin else "否",
                "是" if c.pay_first else "否",
            ]
            for c in certs
        ]
        output = export_csv(headers, rows)
        return "certifications.csv", output.getvalue().encode("utf-8-sig")

    @staticmethod
    def get_xuexin_guide() -> XuexinGuideResponse:
        return XuexinGuideResponse(
            title="学信网验证引导",
            description="部分认证类型（如人社职业技能等级认定）需要在学信网完成学籍验证后方可报名。",
            url="https://www.chsi.com.cn/",
            steps=[
                "登录学信网（https://www.chsi.com.cn/）",
                "进入「学信档案」→「学籍查询」",
                "下载《教育部学籍在线验证报告》",
                "报名时上传该报告用于资格审核",
            ],
        )
