from datetime import datetime, timezone
from types import SimpleNamespace

from openpyxl import load_workbook

from app.services.renshe_export import RensheExportService
from app.services.renshe_export_definition import BATCH_INFO_FILENAME, batch_info_rows


def _plan():
    return SimpleNamespace(
        name="第一期",
        apply_start=datetime(2026, 11, 1, 1, 0, tzinfo=timezone.utc),
        apply_end=datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc),
        exam_date=datetime(2026, 12, 20, 9, 0, tzinfo=timezone.utc),
        exam_date_note="暂定",
        exam_org_name="四川省职业技能鉴定中心",
        exam_org_code="SC-ORG",
        exam_location="测试考点",
        contact_name="王老师",
        contact_phone="13800138000",
    )


def test_batch_info_rows_contain_exam_org_and_note():
    rows = dict(batch_info_rows(_plan()))
    assert rows["报名开始"] == "2026-11-01 09:00"
    assert rows["考试时间备注"] == "暂定"
    assert rows["考试机构"] == "四川省职业技能鉴定中心"
    assert rows["考试机构代码"] == "SC-ORG"


def test_batch_info_workbook_is_generated(tmp_path):
    path = tmp_path / BATCH_INFO_FILENAME
    RensheExportService._write_batch_info_workbook(_plan(), path)

    sheet = load_workbook(path).active
    values = {row[0]: row[1] for row in sheet.iter_rows(min_row=2, values_only=True)}
    assert values["考试机构"] == "四川省职业技能鉴定中心"
    assert values["考试时间备注"] == "暂定"
    assert all(value != "None" for value in values.values())


def test_batch_info_cells_are_text_not_formulas(tmp_path):
    plan = _plan()
    plan.name = '=HYPERLINK("https://example.test","点击")'
    plan.exam_org_name = "=WEBSERVICE(\"https://example.test\")"
    path = tmp_path / BATCH_INFO_FILENAME
    RensheExportService._write_batch_info_workbook(plan, path)

    sheet = load_workbook(path).active
    cells = {
        row[0].value: row[1]
        for row in sheet.iter_rows(min_row=2)
    }
    assert cells["批次名称"].data_type == "s"
    assert cells["考试机构"].data_type == "s"


def test_batch_info_appends_multi_project_rows_as_text(tmp_path):
    path = tmp_path / BATCH_INFO_FILENAME
    RensheExportService._write_batch_info_workbook(
        _plan(),
        path,
        [("报考项目", "=HYPERLINK(\"https://example.test\",\"项目\") · 四级 · ¥380.00")],
    )
    sheet = load_workbook(path).active
    rows = list(sheet.iter_rows(min_row=2))
    project = next(row for row in rows if row[0].value == "报考项目")
    assert project[1].value == '=HYPERLINK("https://example.test","项目") · 四级 · ¥380.00'
    assert project[1].data_type == "s"
