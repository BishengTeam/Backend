"""运营模块权限细分与投递下线的静态契约测试。

运营中心重构（2026-08）后：
- zones/banners → homepage:*
- jobs → job:*
- training → training:*
- activities → activity:*
- 认证产品保持 content:*（cert_admin 依赖）
- 小程序岗位投递端点已移除
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[2]


class RouteInfo(NamedTuple):
    method: str
    path: str
    permission: str | None


def _parse_routes(module_path: str) -> list[RouteInfo]:
    source = (REPO_ROOT / module_path).read_text()
    tree = ast.parse(source)
    routes: list[RouteInfo] = []
    prefix = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "router"
                    and isinstance(node.value, ast.Call)
                    and _call_name(node.value) == "APIRouter"
                ):
                    prefix = _kw(node.value, "prefix") or ""
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call) or _call_name(deco) not in {
                "get", "post", "put", "patch", "delete"
            }:
                continue
            route_path = (deco.args[0].value if deco.args else "") or ""
            permission = None
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and _call_name(sub) == "require_permission":
                    permission = sub.args[0].value
            routes.append(
                RouteInfo(_call_name(deco).upper(), f"{prefix}{route_path}", permission)
            )
    return routes


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def _kw(call: ast.Call, name: str) -> str | None:
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def test_zones_use_homepage_permissions() -> None:
    routes = _parse_routes("app/api/admin/zones.py")
    assert any(r.path == "/zones" and r.method == "GET" and r.permission == "homepage:list" for r in routes)
    assert all(
        r.permission in {"homepage:list", "homepage:write"} for r in routes
    )


def test_banners_use_homepage_permissions() -> None:
    routes = _parse_routes("app/api/admin/banners.py")
    assert routes and all(
        r.permission in {"homepage:list", "homepage:write"} for r in routes
    )


def test_jobs_use_job_permissions() -> None:
    routes = _parse_routes("app/api/admin/jobs.py")
    assert all(r.permission in {"job:list", "job:write"} for r in routes)


def test_training_use_training_permissions() -> None:
    routes = _parse_routes("app/api/admin/training.py")
    assert all(r.permission in {"training:list", "training:write"} for r in routes)


def test_activities_use_activity_permissions_and_expose_registrations() -> None:
    routes = _parse_routes("app/api/admin/activities.py")
    assert all(r.permission in {"activity:list", "activity:write"} for r in routes)
    assert any(
        r.method == "GET"
        and r.path.endswith("/{activity_id}/registrations")
        and r.permission == "activity:list"
        for r in routes
    )


def test_cert_products_keep_content_permissions() -> None:
    routes = _parse_routes("app/api/admin/cert_products.py")
    assert routes and all(
        r.permission and r.permission.startswith("content:") for r in routes
    )


def test_user_job_apply_endpoint_is_removed() -> None:
    source = (REPO_ROOT / "app/api/job.py").read_text()
    assert "apply" not in source
    assert "get_current_user" not in source


def test_job_service_apply_is_deprecated() -> None:
    source = (REPO_ROOT / "app/services/job.py").read_text()
    assert "已弃用" in source and "async def apply" in source
