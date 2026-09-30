"""NISP export service: generate Excel matching the NISP报名表.xlsx template.

Level 1 sheet: 序号/姓名/拼音/专业/学校/身份证号/手机/邮箱/省份/培训种类/身份证双面/寸照
Level 2 sheet: 序号/培训机构/姓名/拼音/学校/身份证号/考试类型/性别/年龄/学历/专业/手机/邮箱/省份/地址/邮编/身份证双面/寸照/学籍报告/申请表
"""

import hashlib
import io
import uuid
from datetime import datetime, timedelta, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.nisp.src import NispRegistration

NISP_LEVEL1_HEADERS = [
    "序号", "姓名", "拼音", "专业", "学校名称/单位名称",
    "身份证号(18位)", "手机号码", "邮箱", "报考省份", "培训种类",
    "身份证双面", "寸照",
]

NISP_LEVEL2_HEADERS = [
    "序号", "培训机构", "姓名", "拼音", "学校名称",
    "身份证号(18位)", "考试类型", "性别", "年龄", "最高学历",
    "专业", "移动电话", "邮箱", "省份", "地址", "邮编",
    "身份证双面照片", "寸照", "学籍报告", "NISP二级考试报名申请表",
]

HEADER_FILL = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
HEADER_FONT = Font(name="微软雅黑", size=10, bold=True)
BODY_FONT = Font(name="微软雅黑", size=10)
THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)
CENTER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)


class NispExportService:

    def build_excel(
        self,
        registrations: list[NispRegistration],
        *,
        level: str,
        batch_name: str = "",
    ) -> bytes:
        """Build an in-memory Excel matching the NISP template."""
        wb = Workbook()
        if level == "1":
            ws = wb.active
            ws.title = "NISP一级报名表"
            self._write_level1(ws, registrations)
        else:
            ws = wb.active
            ws.title = "NISP二级报名表"
            self._write_level2(ws, registrations)

        # Set column widths
        for col_idx in range(1, len(NISP_LEVEL1_HEADERS if level == "1" else NISP_LEVEL2_HEADERS) + 1):
            ws.column_dimensions[get_column_letter(col_idx)].width = 16

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.read()

    def _write_level1(self, ws, registrations: list[NispRegistration]) -> None:
        ws.append(NISP_LEVEL1_HEADERS)
        self._style_header(ws, len(NISP_LEVEL1_HEADERS))

        for idx, reg in enumerate(registrations, 1):
            s = reg.candidate_snapshot
            row = [
                idx,
                s.get("name", ""),
                s.get("pinyin", ""),
                s.get("major", ""),
                s.get("school", ""),
                s.get("id_card", reg.candidate_idcard),
                s.get("phone", ""),
                s.get("email", ""),
                s.get("province", ""),
                s.get("training_type", "NISP一级"),
                "已上传" if self._has_material(reg, "id_card_both_sides") else "未上传",
                "已上传" if self._has_material(reg, "portrait_photo") else "未上传",
            ]
            ws.append(row)
            self._style_row(ws, ws.max_row, len(NISP_LEVEL1_HEADERS))

    def _write_level2(self, ws, registrations: list[NispRegistration]) -> None:
        ws.append(NISP_LEVEL2_HEADERS)
        self._style_header(ws, len(NISP_LEVEL2_HEADERS))

        for idx, reg in enumerate(registrations, 1):
            s = reg.candidate_snapshot
            row = [
                idx,
                s.get("institution", ""),
                s.get("name", ""),
                s.get("pinyin", ""),
                s.get("school", ""),
                s.get("id_card", reg.candidate_idcard),
                s.get("training_type", "NISP二级"),
                s.get("gender", ""),
                s.get("age", ""),
                s.get("education", ""),
                s.get("major", ""),
                s.get("phone", ""),
                s.get("email", ""),
                s.get("province", ""),
                s.get("address", ""),
                s.get("zip_code", ""),
                "已上传" if self._has_material(reg, "id_card_both_sides") else "未上传",
                "已上传" if self._has_material(reg, "portrait_photo") else "未上传",
                "已上传" if self._has_material(reg, "xuexin_report") else "未上传",
                "已上传" if self._has_material(reg, "application_form") else "未上传",
            ]
            ws.append(row)
            self._style_row(ws, ws.max_row, len(NISP_LEVEL2_HEADERS))

    @staticmethod
    def _has_material(reg: NispRegistration, material_type: str) -> bool:
        keys = reg.material_keys or {}
        return bool(keys.get(material_type))

    @staticmethod
    def _style_header(ws, col_count: int) -> None:
        for col in range(1, col_count + 1):
            cell = ws.cell(row=1, column=col)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.border = THIN_BORDER
            cell.alignment = CENTER_ALIGN
        ws.row_dimensions[1].height = 28

    @staticmethod
    def _style_row(ws, row: int, col_count: int) -> None:
        for col in range(1, col_count + 1):
            cell = ws.cell(row=row, column=col)
            cell.font = BODY_FONT
            cell.border = THIN_BORDER
            cell.alignment = CENTER_ALIGN
