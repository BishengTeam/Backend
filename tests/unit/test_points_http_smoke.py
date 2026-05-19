from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_points_api_module():
    previous_modules = {
        name: sys.modules.get(name)
        for name in (
            "app.middleware",
            "app.middleware.auth",
            "app.models",
            "app.models.user",
            "app.services",
            "app.services.points",
        )
    }

    auth_package = types.ModuleType("app.middleware")
    auth_package.__path__ = []
    auth_module = types.ModuleType("app.middleware.auth")

    async def fake_current_user():
        return SimpleNamespace(id=7)

    auth_module.get_current_user = fake_current_user

    models_package = types.ModuleType("app.models")
    models_package.__path__ = []
    user_module = types.ModuleType("app.models.user")

    class User:
        pass

    user_module.User = User

    services_package = types.ModuleType("app.services")
    services_package.__path__ = []
    points_service_module = types.ModuleType("app.services.points")

    class PlaceholderPointsService:
        pass

    points_service_module.PointsService = PlaceholderPointsService

    sys.modules["app.middleware"] = auth_package
    sys.modules["app.middleware.auth"] = auth_module
    sys.modules["app.models"] = models_package
    sys.modules["app.models.user"] = user_module
    sys.modules["app.services"] = services_package
    sys.modules["app.services.points"] = points_service_module

    try:
        spec = importlib.util.spec_from_file_location(
            "points_api_under_test",
            REPO_ROOT / "app" / "api" / "points.py",
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Unable to load app/api/points.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class FakePointsService:
    async def get_balance(self, user_id: int):
        from app.schemas.points import PointsBalanceResponse

        assert user_id == 7
        return PointsBalanceResponse(balance=35)

    async def list_history(self, user_id: int, *, page: int = 1, page_size: int = 20):
        from app.schemas.common import PaginatedData
        from app.schemas.points import PointsHistoryResponse

        assert user_id == 7
        return PaginatedData[PointsHistoryResponse](
            items=[
                PointsHistoryResponse(
                    id=11,
                    action_type="claim_daily_checkin",
                    amount=5,
                    balance_after=35,
                    description="daily checkin",
                    source_type="points_claim",
                    source_id="daily_checkin:2026-05-19",
                    created_at=datetime.now(timezone.utc),
                )
            ],
            total=1,
            page=page,
            page_size=page_size,
        )

    async def claim_points(self, user_id: int, data: Any):
        from app.schemas.points import PointsClaimResponse

        assert user_id == 7
        return PointsClaimResponse(
            claimed=True,
            scene=data.scene,
            amount=5,
            balance=40,
            history_id=12,
        )

    async def redeem_points(self, user_id: int, data: Any):
        from app.schemas.points import PointsRedeemResponse

        assert user_id == 7
        return PointsRedeemResponse(
            redeem_type=data.redeem_type,
            amount=data.amount,
            balance=25,
            history_id=13,
        )


class PointsHttpSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.points_api = _load_points_api_module()

    def setUp(self) -> None:
        self.original_points_service = self.points_api.PointsService
        self.points_api.PointsService = FakePointsService
        self.addCleanup(self._restore_points_service)

        app = FastAPI()
        app.include_router(self.points_api.router, prefix="/api")

        async def fake_current_user():
            return SimpleNamespace(id=7)

        app.dependency_overrides[self.points_api.get_current_user] = fake_current_user
        self.client = TestClient(app)

    def _restore_points_service(self) -> None:
        self.points_api.PointsService = self.original_points_service

    def test_points_routes_depend_on_current_user(self):
        expected_paths = {"/points", "/points/history", "/points/claim", "/points/redeem"}
        protected_paths: set[str] = set()

        for route in self.points_api.router.routes:
            if getattr(route, "path", None) not in expected_paths:
                continue
            dependencies = {dependency.call for dependency in route.dependant.dependencies}
            if self.points_api.get_current_user in dependencies:
                protected_paths.add(route.path)

        self.assertEqual(protected_paths, expected_paths)

    def test_points_http_smoke_returns_uniform_success_payloads(self):
        cases = [
            ("GET", "/api/points", None),
            ("GET", "/api/points/history?page=1&page_size=10", None),
            ("POST", "/api/points/claim", {"scene": "daily_checkin"}),
            ("POST", "/api/points/redeem", {"redeem_type": "course", "amount": 10, "target_id": 1}),
        ]

        for method, path, body in cases:
            with self.subTest(endpoint=f"{method} {path}"):
                response = self.client.request(
                    method,
                    path,
                    json=body,
                    headers={"Authorization": "Bearer test-token"},
                )
                payload = response.json()

                self.assertEqual(response.status_code, 200)
                self.assertEqual(payload["code"], 0)
                self.assertEqual(payload["message"], "ok")
                self.assertIsNotNone(payload["data"])


if __name__ == "__main__":
    unittest.main()
