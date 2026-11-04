import pytest

from scripts.configure_course_oss import _runtime_extra_origins, _validate_origin


def test_extra_course_oss_origins_accept_deployment_http_test_entry():
    runtime = {
        "COURSE_OSS_EXTRA_ORIGINS": (
            " http://8.156.64.81:20303 , https://video.example.com/ ,, "
        )
    }

    assert _runtime_extra_origins(runtime) == {
        "http://8.156.64.81:20303",
        "https://video.example.com",
    }


def test_course_oss_http_origin_requires_explicit_runtime_configuration():
    assert _validate_origin("https://admin.example.com", allow_http=False) == (
        "https://admin.example.com"
    )

    with pytest.raises(SystemExit, match="invalid HTTPS origin"):
        _validate_origin("http://8.156.64.81:20303", allow_http=False)


@pytest.mark.parametrize(
    "origin",
    [
        "ftp://example.com",
        "https://example.com/path",
        "https://user@example.com",
        "https://example.com?x=1",
    ],
)
def test_course_oss_origin_must_be_a_safe_origin(origin):
    with pytest.raises(SystemExit, match="invalid"):
        _validate_origin(origin, allow_http=True)
