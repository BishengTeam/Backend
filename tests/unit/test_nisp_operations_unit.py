import ast
from datetime import datetime, timezone
import unittest
from pathlib import Path
from typing import get_args

from app.schemas.nisp import NispRegistrationStatus
from app.api.admin.nisp import get_export_signed_url
from app.services.nisp_export import NispExportService


REPO_ROOT = Path(__file__).resolve().parents[2]


def _source(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _functions(source: str) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    tree = ast.parse(source)
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class NispOperationsTests(unittest.TestCase):
    def test_registration_response_accepts_every_database_refund_status(self):
        statuses = set(get_args(NispRegistrationStatus))

        self.assertIn("pending_refund_confirmation", statuses)
        self.assertIn("refund_processing", statuses)
        self.assertIn("refunded_closed", statuses)

    def test_refund_provider_payloads_are_parsed_with_wechat_pay_refund(self):
        source = _source("app/services/nisp_refund.py")

        self.assertNotIn("parse_refund_result", source)
        self.assertNotIn("result.out_trade_no != refund.out_trade_no", source)
        self.assertIn("expected_out_trade_no = order.out_trade_no if order else None", source)
        self.assertEqual(source.count("WechatPayRefund.from_payload(raw)"), 2)
        self.assertIn("async def handle_callback_raw", source)
        self.assertIn("parse_refund_notification", source)

    def test_nisp_refund_notification_is_dispatched_by_payment_callback(self):
        source = _source("app/api/payment.py")
        callback = _functions(source)["refund_callback"]
        body = ast.unparse(callback)

        self.assertIn("NispRefundService().handle_callback_raw", body)
        self.assertIn("RensheRefundService().handle_callback_raw", body)

    def test_nisp_export_worker_is_started_and_cancelled_with_application(self):
        source = _source("app/main.py")

        self.assertIn("from app.services.nisp_export import nisp_export_worker_loop", source)
        self.assertIn(
            "nisp_export_task = asyncio.create_task(nisp_export_worker_loop())",
            source,
        )
        self.assertIn("nisp_export_task.cancel()", source)
        self.assertIn("await nisp_export_task", source)


class NispSignedUrlRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_url_route_builds_expiry_without_undefined_helper(self):
        original = NispExportService.signed_url

        async def fake_signed_url(self, job_id: int) -> str:
            return "https://example.test/nisp-export.xlsx?signature=test"

        NispExportService.signed_url = fake_signed_url
        try:
            response = await get_export_signed_url(job_id=2, _admin=object())
        finally:
            NispExportService.signed_url = original

        self.assertEqual(response.code, 0)
        self.assertEqual(response.data.url, "https://example.test/nisp-export.xlsx?signature=test")
        self.assertGreater(response.data.expires_at, datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
