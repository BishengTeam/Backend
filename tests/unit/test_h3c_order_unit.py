import ast
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class H3cOrderTests(unittest.TestCase):
    def test_request_uses_batch_and_type_conditional_materials(self):
        source = (REPO_ROOT / "app/schemas/h3c_registration.py").read_text("utf-8")
        tree = ast.parse(source)
        request_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "H3cOrderCreate"
        )
        fields = {
            stmt.target.id
            for stmt in request_class.body
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
        }

        self.assertIn("batch_id", fields)
        self.assertIn("registration_type", fields)
        self.assertIn("coupon_code", fields)
        self.assertIn("verify_code", fields)
        self.assertIn("coupon_proof_key", fields)
        self.assertIn("student_proof_key", fields)
        self.assertNotIn("plan_id", fields)
        self.assertNotIn("exam_code", fields)
        self.assertNotIn("exam_datetime", fields)
        self.assertNotIn("identity_tag", fields)
        self.assertIn("全额报名不能提交考券或学生证明材料", source)
        self.assertIn("考券报名必须提供考券号和优惠券证明图片", source)
        self.assertIn("学生报名必须提供学信网在线验证码和学生证明图片", source)

    def test_registration_service_uses_order_and_inventory_foundations(self):
        source = (REPO_ROOT / "app/services/h3c_registration.py").read_text("utf-8")
        self.assertIn("async with db.begin():", source)
        self.assertIn("PlanEnrollmentService().lock_enrollable_plan", source)
        self.assertIn('expected_vendor="H3C"', source)
        self.assertIn("lock_inventory", source)
        self.assertIn('inventory_type="h3c_batch"', source)
        self.assertIn('ref_code=f"h3c-batch-{batch.id}"', source)
        self.assertIn("INVENTORY_LOCK_ACTION", source)
        self.assertIn('order_kind="certification"', source)
        self.assertIn("generate_out_trade_no(\"H3C\")", source)
        self.assertIn("confirm_inventory_sale", source)
        self.assertIn("H3cMaterialUpload", source)
        self.assertIn("status = \"pending_review\"", source)
        create_order_source = source[
            source.index("async def create_order") : source.index(
                "async def list_registrations"
            )
        ]
        resubmission_source = source[
            source.index("async def resubmit_materials") : source.index(
                "async def review"
            )
        ]
        review_source = source[
            source.index("async def review") : source.index(
                "async def close_registration"
            )
        ]
        self.assertNotIn("H3cResubmissionCreate.model_validate", create_order_source)
        self.assertIn(
            "H3cResubmissionCreate.model_validate(data)",
            resubmission_source,
        )
        self.assertIn(
            "H3cReviewDecision.model_validate(decision_data)",
            review_source,
        )

    def test_h3c_response_exposes_registration_and_payment_fields(self):
        source = (REPO_ROOT / "app/schemas/h3c_registration.py").read_text("utf-8")
        tree = ast.parse(source)
        response_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "H3cRegistrationResponse"
        )
        fields = {
            stmt.target.id
            for stmt in response_class.body
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
        }

        self.assertIn("order_id", fields)
        self.assertIn("order_status", fields)
        self.assertIn("out_trade_no", fields)
        self.assertIn("price_cents", fields)
        self.assertIn("resubmission_count", fields)
        self.assertIn("resubmission_due_at", fields)
        self.assertIn("materials", fields)
        self.assertIn("latest_review", fields)
