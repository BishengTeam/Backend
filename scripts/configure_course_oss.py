"""Configure the private OSS bucket CORS required by course browser uploads."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlsplit


def _read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line or raw_line.startswith("#"):
            continue
        key, separator, value = raw_line.partition("=")
        if not separator or key in result:
            raise SystemExit(f"invalid runtime configuration line: {raw_line}")
        result[key] = value
    return result


def _required(runtime: dict[str, str], key: str) -> str:
    value = runtime.get(key, "").strip()
    if not value:
        raise SystemExit(f"{key} is not configured")
    return value


def _validate_origin(origin: str, *, allow_http: bool) -> str:
    normalized = origin.rstrip("/")
    parsed = urlsplit(normalized)
    allowed_schemes = {"https", "http"} if allow_http else {"https"}
    if (
        parsed.scheme not in allowed_schemes
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        suffix = " HTTPS origin" if not allow_http else " HTTP(S) origin"
        raise SystemExit(f"invalid{suffix}: {origin}")
    return normalized


def _runtime_extra_origins(runtime: dict[str, str]) -> set[str]:
    """Read deployment-controlled origins, including temporary HTTP test entries."""
    values = runtime.get("COURSE_OSS_EXTRA_ORIGINS", "").split(",")
    return {
        _validate_origin(value.strip(), allow_http=True)
        for value in values
        if value.strip()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installation-dir", type=Path, required=True)
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        help="additional HTTPS origin; ADMIN_ORIGIN is always included",
    )
    args = parser.parse_args()

    installation_dir = args.installation_dir.resolve()
    runtime = _read_env(installation_dir / "runtime.env")
    origins = {
        _validate_origin(_required(runtime, "ADMIN_ORIGIN"), allow_http=False),
        *(_validate_origin(origin, allow_http=False) for origin in args.allow_origin),
        *_runtime_extra_origins(runtime),
    }
    import oss2
    from oss2.models import BucketCors, CorsRule

    access_id = (installation_dir / "secrets" / "aliyun_oss_access_key_id").read_text().strip()
    access_secret = (
        installation_dir / "secrets" / "aliyun_oss_access_key_secret"
    ).read_text().strip()
    bucket = oss2.Bucket(
        oss2.Auth(access_id, access_secret),
        _required(runtime, "ALIYUN_OSS_ENDPOINT"),
        _required(runtime, "ALIYUN_OSS_BUCKET"),
    )
    rule = CorsRule(
        allowed_origins=sorted(origins),
        allowed_methods=["GET", "PUT", "POST", "DELETE", "HEAD"],
        allowed_headers=[
            "Authorization",
            "Content-Type",
            "Content-Length",
            "x-oss-date",
            "x-oss-user-agent",
            "x-oss-security-token",
            "x-oss-meta-*",
            "Range",
        ],
        expose_headers=[
            "ETag",
            "x-oss-request-id",
            "Content-Length",
            "Content-Range",
            "Accept-Ranges",
        ],
        max_age_seconds=3600,
    )
    bucket.put_bucket_cors(BucketCors([rule]))
    configured = bucket.get_bucket_cors()
    actual_origins = {
        origin
        for rule in configured.rules
        for origin in rule.allowed_origins
    }
    missing = origins - actual_origins
    if missing:
        raise SystemExit("course OSS CORS verification failed")
    print("course_oss_cors=ok")
    print("origins=" + ",".join(sorted(origins)))


if __name__ == "__main__":
    main()
