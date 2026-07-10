import ast
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.schemas.admin_certification import (
    AdminCertificationCreate,
    AdminCertificationListItem,
    AdminCertificationUpdate,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


class AdminCertificationPricingTests(unittest.TestCase):
    def test_admin_certification_schema_exposes_pricing_workflow_fields(self):
        create_fields = set(AdminCertificationCreate.model_fields)
        update_fields = set(AdminCertificationUpdate.model_fields)
        list_fields = set(AdminCertificationListItem.model_fields)

        expected = {"code", "vendor", "normal_price", "student_price", "is_active"}
        self.assertTrue(expected <= create_fields)
        self.assertTrue(expected <= update_fields)
        self.assertTrue(expected <= list_fields)
        self.assertNotIn("requires_xuexin", create_fields)
        self.assertNotIn("pay_first", create_fields)
        self.assertNotIn("name", create_fields)
        self.assertNotIn("chinese_name", create_fields)

    def test_admin_certification_prices_must_be_non_negative(self):
        with self.assertRaises(ValidationError):
            AdminCertificationCreate(
                code="H3C-NE",
                vendor="H3C",
                normal_price=-1,
                student_price=100,
            )
        with self.assertRaises(ValidationError):
            AdminCertificationUpdate(student_price=-1)

    def test_admin_certification_service_syncs_normal_and_student_prices(self):
        source = (REPO_ROOT / "app/services/admin_certification.py").read_text(encoding="utf-8")

        self.assertIn('PRICE_TIER_NORMAL = "normal"', source)
        self.assertIn('PRICE_TIER_STUDENT = "student"', source)
        self.assertIn("async with db.begin():", source)
        self.assertIn("PriceConfig(", source)
        self.assertIn("product_type=data.code", source)
        self.assertIn("user_type=PRICE_TIER_NORMAL", source)
        self.assertIn("price=data.normal_price", source)
        self.assertIn("user_type=PRICE_TIER_STUDENT", source)
        self.assertIn("price=data.student_price", source)
        self.assertIn("_sync_prices_for_code_change", source)

    def test_admin_certification_api_returns_aggregated_item(self):
        source = (REPO_ROOT / "app/api/admin/certifications.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        response_models = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if node.name not in {"create_certification", "update_certification"}:
                continue
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    for keyword in decorator.keywords:
                        if keyword.arg == "response_model":
                            response_models.append(ast.unparse(keyword.value))

        self.assertEqual(
            response_models,
            ["APIResponse[AdminCertificationListItem]", "APIResponse[AdminCertificationListItem]"],
        )


if __name__ == "__main__":
    unittest.main()

