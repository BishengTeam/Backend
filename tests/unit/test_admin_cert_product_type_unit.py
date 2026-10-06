import unittest
from pathlib import Path
from typing import get_args

from pydantic import ValidationError

from app.schemas.admin_cert_product import CertProductCreate, CertType
from app.services.admin_cert_product import TYPE_LABELS


REPO_ROOT = Path(__file__).resolve().parents[2]


def _payload(cert_type: str) -> dict:
    return {
        "type": cert_type,
        "code": "NISP-1",
        "name": "NISP",
        "chinese_name": "NISP 国家信息安全水平考试（一级/二级）",
        "prices": [
            {"user_type": "student", "price_cents": 0},
            {"user_type": "normal", "price_cents": 0},
        ],
    }


class AdminCertProductTypeTests(unittest.TestCase):
    def test_product_admin_accepts_all_current_certification_vendors(self):
        expected = {"h3c", "renshe", "nisp", "sangfor"}
        self.assertEqual(set(get_args(CertType)), expected)
        for cert_type in expected:
            self.assertEqual(CertProductCreate(**_payload(cert_type)).type, cert_type)

    def test_product_admin_rejects_unknown_vendor(self):
        with self.assertRaises(ValidationError):
            CertProductCreate(**_payload("unknown"))

    def test_stats_and_api_copy_include_new_vendor_types(self):
        service_source = (
            REPO_ROOT / "app/services/admin_cert_product.py"
        ).read_text(encoding="utf-8")
        api_source = (REPO_ROOT / "app/api/admin/cert_products.py").read_text(
            encoding="utf-8"
        )

        self.assertEqual(TYPE_LABELS.get("nisp"), "NISP 认证")
        self.assertEqual(TYPE_LABELS.get("sangfor"), "深信服认证")
        self.assertIn("nisp / sangfor", api_source)
        self.assertIn("type: CertType | None", api_source)
        self.assertIn('TYPE_LABELS: dict[str, str]', service_source)


if __name__ == "__main__":
    unittest.main()
