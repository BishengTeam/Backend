"""Fixed document IDs; callers never supply a filesystem path."""
from pathlib import Path
from app.port.config import settings
from app.port.exceptions import NotFoundException

DOCUMENTS = {
    "conditions": ("职业技能等级认定职业工种申报条件.pdf", "application/pdf"),
    "answers": ("职业技能等级认定申报条件相关解答.pdf", "application/pdf"),
    "declaration": ("四川省职业技能等级认定个人申报表.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "registration": ("报名信息.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "notes": ("注意.txt", "text/plain"),
}


def reference_list(grade=None):
    locators = {"conditions": "申报条件及相关职业、专业说明", "answers": "第4—5页：三级路径" if grade == 3 else "第2页：五级路径" if grade == 5 else "申报条件、材料规范及院校范围",
                "declaration": "个人申报字段及承诺内容参考", "registration": "报送字段及字典", "notes": "多职业业务说明"}
    return [{"document_id": key, "title": Path(filename).stem, "locator": locators[key],
             "action": "preview" if mime in {"application/pdf", "text/plain"} else "download"} for key, (filename, mime) in DOCUMENTS.items()]


def reference_file(document_id):
    if document_id not in DOCUMENTS:
        raise NotFoundException("资料依据")
    filename, mime = DOCUMENTS[document_id]
    root = Path(settings.RENSHE_REFERENCE_DIR).resolve() if settings.RENSHE_REFERENCE_DIR else Path(__file__).resolve().parents[2] / "docs" / "renshe" / "references"
    root = root.resolve()
    path = (root / filename).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise NotFoundException("资料依据文件未部署，请联系管理员")
    return path, mime
