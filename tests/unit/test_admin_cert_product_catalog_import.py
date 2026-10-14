from pathlib import Path

from app.services.admin_cert_product import parse_h3c_price_sheet


def test_parse_h3c_price_sheet_reads_catalog_and_price_rows():
    root = Path(__file__).resolve().parents[2]
    content = (root / "docs/h3c/考试认证价格.xlsx").read_bytes()

    rows = parse_h3c_price_sheet(content)
    by_code = {row["code"]: row for row in rows}

    assert "GB0-192" in by_code
    assert by_code["GB0-192"]["name"]
    assert by_code["GB0-192"]["normal_price_yuan"] == 1200
    assert by_code["GB0-713"]["student_price_yuan"] == 500
    assert by_code["GB0-192"]["pass_score"] == 600
    assert by_code["GB0-170"]["cert_validity_years"] == 3
