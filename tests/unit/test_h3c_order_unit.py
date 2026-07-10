import ast
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class H3cOrderTests(unittest.TestCase):
    def test_h3c_order_service_uses_order_foundation(self):
        source = (REPO_ROOT / "app/services/h3c_order.py").read_text(encoding="utf-8")
        create_order_source = source[
            source.index("async def create_order") : source.index("return H3cOrderResponse")
        ]

        self.assertIn("async with db.begin():", create_order_source)
        self.assertIn("UserRealname.user_id == user_id", create_order_source)
        self.assertIn('UserRealname.status == "verified"', create_order_source)
        self.assertIn('raise BusinessException("请先完成实名认证")', create_order_source)
        self.assertIn("product_type = data.exam_code", create_order_source)
        self.assertIn("Certification.code == product_type", create_order_source)
        self.assertIn('Certification.vendor == "H3C"', create_order_source)
        self.assertIn("price_tier = resolve_price_tier(identity.user_type)", create_order_source)
        self.assertIn("PriceConfig.product_type == product_type", create_order_source)
        self.assertIn("PriceConfig.user_type == price_tier", create_order_source)
        self.assertIn("inventory_change = await lock_certification_inventory(db, product_type)", create_order_source)
        self.assertLess(
            create_order_source.index("inventory_change = await lock_certification_inventory"),
            create_order_source.index("order = Order("),
        )
        self.assertIn("inventory_id=inventory_change.inventory_id", create_order_source)
        self.assertIn("price=price_rows[0].price", create_order_source)
        self.assertIn('status="pending"', create_order_source)
        self.assertIn("out_trade_no=f", create_order_source)
        self.assertIn("add_inventory_record(", create_order_source)
        self.assertIn("action=INVENTORY_LOCK_ACTION", create_order_source)
        self.assertNotIn('H3C_PRODUCT_TYPE = "H3C-RE"', source)
        self.assertNotIn("price=0", create_order_source)
        self.assertNotIn("await db.commit()", create_order_source)

    def test_h3c_response_exposes_payment_and_inventory_fields(self):
        tree = ast.parse((REPO_ROOT / "app/schemas/h3c.py").read_text(encoding="utf-8"))
        response_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "H3cOrderResponse"
        )
        fields = {
            stmt.target.id
            for stmt in response_class.body
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
        }

        self.assertIn("out_trade_no", fields)
        self.assertIn("inventory_id", fields)
