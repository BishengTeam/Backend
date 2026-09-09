"""Unit tests for P0 agreement template module (schemas, models, routes)."""

import pytest
from pydantic import ValidationError

from app.main import app
from app.domain.content.src.index import (
    AGREEMENT_TEMPLATE_TYPES,
    AgreementAcceptance,
    AgreementTemplate,
)
from app.schemas.agreement_template import (
    AgreementAcceptItem,
    AgreementAcceptRequest,
    AgreementTemplatePublic,
)
from app.schemas.admin_agreement_template import (
    AdminAgreementTemplateCreate,
    AdminAgreementTemplateUpdate,
)


EXPECTED_USER_ENDPOINTS = {
    ("/api/agreement-templates", "get"),
    ("/api/agreement-acceptances", "post"),
    ("/api/agreement-acceptances", "get"),
}

EXPECTED_ADMIN_ENDPOINTS = {
    ("/admin/agreement-templates", "get"),
    ("/admin/agreement-templates", "post"),
    ("/admin/agreement-templates/{template_id}", "put"),
    ("/admin/agreement-templates/{template_id}/archive", "put"),
}


def _openapi_endpoints() -> set[tuple[str, str]]:
    spec = app.openapi()
    endpoints: set[tuple[str, str]] = set()
    for path, ops in spec["paths"].items():
        for method in ops:
            if method in {"get", "post", "put", "delete", "patch"}:
                endpoints.add((path, method))
    return endpoints


class TestAgreementTemplateModels:
    def test_template_table_and_constraints(self):
        assert AgreementTemplate.__tablename__ == "agreement_template"
        names = {c.name for c in AgreementTemplate.__table__.constraints}
        assert "ck_agreement_template_type" in names
        assert "ck_agreement_template_status" in names

    def test_acceptance_table_and_unique_constraint(self):
        assert AgreementAcceptance.__tablename__ == "agreement_acceptance"
        names = {c.name for c in AgreementAcceptance.__table__.constraints}
        assert "uq_agreement_acceptance_user_template" in names

    def test_template_types_constant(self):
        assert AGREEMENT_TEMPLATE_TYPES == ("user_terms", "privacy", "identity_auth")


class TestAgreementTemplateSchemas:
    def test_public_template_accepts_known_types_only(self):
        item = AgreementTemplatePublic(
            type="user_terms", title="用户服务协议", content="正文", version=1
        )
        assert item.version == 1
        with pytest.raises(ValidationError):
            AgreementTemplatePublic(
                type="training", title="培训协议", content="正文", version=1
            )

    def test_accept_request_validates_items(self):
        req = AgreementAcceptRequest(
            items=[
                AgreementAcceptItem(type="user_terms", version=1),
                AgreementAcceptItem(type="privacy", version=1),
            ]
        )
        assert len(req.items) == 2
        with pytest.raises(ValidationError):
            AgreementAcceptRequest(items=[])
        with pytest.raises(ValidationError):
            AgreementAcceptItem(type="user_terms", version=0)

    def test_admin_create_requires_known_type_and_content(self):
        model = AdminAgreementTemplateCreate(
            type="identity_auth", title="实名授权", content="正文"
        )
        assert model.type == "identity_auth"
        with pytest.raises(ValidationError):
            AdminAgreementTemplateCreate(type="unknown", title="t", content="c")
        with pytest.raises(ValidationError):
            AdminAgreementTemplateCreate(type="user_terms", title="t", content="")

    def test_admin_update_requires_title_and_content(self):
        with pytest.raises(ValidationError):
            AdminAgreementTemplateUpdate(title="", content="c")
        with pytest.raises(ValidationError):
            AdminAgreementTemplateUpdate(title="t", content="")


class TestAgreementTemplateRoutes:
    def test_user_endpoints_registered(self):
        endpoints = _openapi_endpoints()
        for expected in EXPECTED_USER_ENDPOINTS:
            assert expected in endpoints, f"missing user endpoint {expected}"

    def test_admin_endpoints_registered(self):
        endpoints = _openapi_endpoints()
        for expected in EXPECTED_ADMIN_ENDPOINTS:
            assert expected in endpoints, f"missing admin endpoint {expected}"

    def test_templates_endpoint_is_public(self):
        spec = app.openapi()
        assert "security" not in spec["paths"]["/api/agreement-templates"]["get"]
