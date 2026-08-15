"""Bootstrap credential rules stay aligned with administrator contracts."""

from pydantic import ValidationError
import pytest

from bootstrap_app.models import BootstrapAdminRequest


def test_bootstrap_admin_normalizes_username_and_marks_password_as_secret() -> None:
    request = BootstrapAdminRequest(
        username="  Root.Operator  ",
        password="Initial-Secure-4827",
    )

    assert request.username == "root.operator"
    assert request.password.get_secret_value() == "Initial-Secure-4827"
    assert "Initial-Secure-4827" not in str(request)


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("1root", "Initial-Secure-4827"),
        ("abc", "Initial-Secure-4827"),
        ("root", "letters-only-password"),
        ("root", "root-Secure-4827"),
        ("root", "password1234"),
    ],
)
def test_bootstrap_admin_rejects_credentials_outside_frozen_policy(
    username: str,
    password: str,
) -> None:
    with pytest.raises(ValidationError):
        BootstrapAdminRequest(username=username, password=password)
