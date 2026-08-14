from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from bootstrap_app.config import BootstrapSettings
from bootstrap_app.installation import (
    BootstrapValidationError,
    InstallationCommitError,
    InstallationStore,
    build_installation_payload,
)
from bootstrap_app.models import (
    BootstrapAdminRequest,
    BootstrapConfigureRequest,
    BootstrapStatusResponse,
)
from bootstrap_app.runtime import BootstrapRuntimeError, create_initial_super_admin
from bootstrap_app.probes import ExternalProbeError, ProbeReport, validate_external_dependencies
from bootstrap_app.state import (
    BootstrapCompletedError,
    BootstrapPhase,
    BootstrapStateError,
    BootstrapStateStore,
)


logger = logging.getLogger("wemini.bootstrap")
STATIC_DIR = Path(__file__).with_name("static")


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message},
        headers={"Cache-Control": "no-store"},
    )


def _public_status(state) -> dict[str, object]:
    return BootstrapStatusResponse.model_validate(state.public_dict()).model_dump()


def create_app(
    settings: BootstrapSettings | None = None,
    *,
    probe_runner: Callable[..., Awaitable[ProbeReport]] = validate_external_dependencies,
    admin_creator: Callable[..., Awaitable[int]] = create_initial_super_admin,
) -> FastAPI:
    bootstrap_settings = settings or BootstrapSettings.from_env()
    state_store = BootstrapStateStore(
        bootstrap_settings.control_dir,
        bootstrap_settings.token,
    )
    installation_store = InstallationStore(
        bootstrap_settings.installation_dir,
        bootstrap_settings.token,
    )
    initial_state = state_store.initialize()
    if (
        initial_state.phase == BootstrapPhase.NEW
        and bootstrap_settings.installation_dir.exists()
    ):
        # Recover the narrow crash window after the atomic installation rename
        # and before the signed state transition.
        fingerprint = installation_store.verify_existing(initial_state.installation_id)
        initial_state = state_store.transition(
            BootstrapPhase.NEW,
            BootstrapPhase.CONFIGURED,
            config_fingerprint=fingerprint,
        )

    application = FastAPI(
        title="weMiniApp one-time bootstrap",
        version="1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.bootstrap_settings = bootstrap_settings
    application.state.bootstrap_state_store = state_store
    application.state.installation_store = installation_store

    @application.middleware("http")
    async def security_boundary(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                too_large = int(content_length) > bootstrap_settings.request_max_bytes
            except ValueError:
                return _error(400, "invalid_content_length", "请求长度无效")
            if too_large:
                return _error(413, "request_too_large", "请求内容过大")
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        # FastAPI's default response includes the rejected input.  That is not
        # acceptable here because the input can be a private key or password.
        fields: list[str] = []
        for error in exc.errors():
            location = error.get("loc") or ()
            safe_location = ".".join(
                str(item) for item in location if item not in {"body", "query"}
            )
            if safe_location and safe_location not in fields:
                fields.append(safe_location[:128])
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_failed",
                "message": "提交内容校验失败",
                "fields": fields[:32],
            },
            headers={"Cache-Control": "no-store"},
        )

    @application.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("bootstrap request failed: %s", type(exc).__name__)
        return _error(500, "internal_error", "初始化服务内部错误")

    def require_token(authorization: str | None = Header(default=None)) -> None:
        supplied = ""
        if authorization and authorization.startswith("Bearer "):
            supplied = authorization[7:].strip()
        try:
            supplied_bytes = supplied.encode("ascii")
        except UnicodeEncodeError:
            supplied_bytes = b""
        if not secrets.compare_digest(supplied_bytes, bootstrap_settings.token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="bootstrap token is invalid",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @application.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, object]:
        return {"status": "ok", "component": "bootstrap", "version": 1}

    @application.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        return RedirectResponse(url="/setup", status_code=307)

    @application.get("/setup", include_in_schema=False)
    async def setup_page() -> FileResponse:
        try:
            state_store.load(allow_completed=False)
        except BootstrapCompletedError:
            raise HTTPException(status_code=410, detail="bootstrap is closed") from None
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    @application.get("/assets/bootstrap.js", include_in_schema=False)
    async def javascript() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "bootstrap.js",
            media_type="application/javascript",
        )

    @application.get("/assets/bootstrap.css", include_in_schema=False)
    async def stylesheet() -> FileResponse:
        return FileResponse(STATIC_DIR / "bootstrap.css", media_type="text/css")

    @application.get(
        "/api/bootstrap/status",
        response_model=BootstrapStatusResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_status() -> dict[str, object]:
        try:
            current = state_store.load(allow_completed=False)
        except BootstrapCompletedError:
            raise HTTPException(status_code=410, detail="bootstrap is closed") from None
        return _public_status(current)

    @application.post(
        "/api/bootstrap/configure",
        response_model=BootstrapStatusResponse,
        dependencies=[Depends(require_token)],
    )
    async def configure(body: BootstrapConfigureRequest) -> dict[str, object]:
        try:
            current = state_store.load(allow_completed=False)
            if current.phase == BootstrapPhase.CONFIGURED:
                return _public_status(current)
            if current.phase != BootstrapPhase.NEW:
                raise HTTPException(status_code=409, detail="bootstrap phase conflict")

            if bootstrap_settings.installation_dir.exists():
                fingerprint = installation_store.verify_existing(
                    current.installation_id
                )
            else:
                secret_files, runtime, recovery_key = build_installation_payload(
                    body,
                    host_deploy_root=bootstrap_settings.host_deploy_root,
                )
                await probe_runner(body, installation_id=current.installation_id)
                try:
                    fingerprint = installation_store.commit(
                        installation_id=current.installation_id,
                        secret_files=secret_files,
                        runtime=runtime,
                        recovery_public_key=recovery_key,
                    )
                except FileExistsError:
                    fingerprint = installation_store.verify_existing(
                        current.installation_id
                    )
            current = state_store.transition(
                BootstrapPhase.NEW,
                BootstrapPhase.CONFIGURED,
                config_fingerprint=fingerprint,
            )
            return _public_status(current)
        except BootstrapCompletedError:
            raise HTTPException(status_code=410, detail="bootstrap is closed") from None
        except BootstrapValidationError as exc:
            logger.warning("bootstrap configuration rejected: %s", type(exc).__name__)
            return _error(422, "configuration_invalid", str(exc))
        except ExternalProbeError as exc:
            state_store.record_failure(exc.code, exc.component)
            logger.warning("bootstrap external probe rejected: %s", exc.component)
            return _error(
                422,
                "external_validation_failed",
                f"外部依赖验证失败：{exc.component} ({exc.code})",
            )
        except InstallationCommitError as exc:
            logger.error("bootstrap installation commit failed: %s", type(exc).__name__)
            return _error(409, "installation_commit_failed", "初始化文件提交失败")
        except BootstrapStateError as exc:
            logger.error("bootstrap state transition failed: %s", type(exc).__name__)
            return _error(409, "state_conflict", "初始化状态冲突")

    @application.post(
        "/api/bootstrap/retry",
        response_model=BootstrapStatusResponse,
        dependencies=[Depends(require_token)],
    )
    async def retry_current_step() -> dict[str, object]:
        try:
            current = state_store.clear_failure()
        except BootstrapCompletedError:
            raise HTTPException(status_code=410, detail="bootstrap is closed") from None
        return _public_status(current)

    @application.post(
        "/api/bootstrap/admin",
        response_model=BootstrapStatusResponse,
        dependencies=[Depends(require_token)],
    )
    async def create_admin(body: BootstrapAdminRequest) -> dict[str, object]:
        try:
            current = state_store.load(allow_completed=False)
            if current.phase == BootstrapPhase.ADMIN_CREATED:
                return _public_status(current)
            if current.phase != BootstrapPhase.AWAITING_ADMIN:
                raise HTTPException(status_code=409, detail="bootstrap phase conflict")
            await admin_creator(
                bootstrap_settings.installation_dir,
                body,
            )
            current = state_store.transition(
                BootstrapPhase.AWAITING_ADMIN,
                BootstrapPhase.ADMIN_CREATED,
            )
            return _public_status(current)
        except BootstrapCompletedError:
            raise HTTPException(status_code=410, detail="bootstrap is closed") from None
        except BootstrapRuntimeError as exc:
            logger.error("bootstrap administrator creation failed: %s", type(exc).__name__)
            return _error(409, "admin_creation_failed", "超级管理员创建失败")
        except BootstrapStateError as exc:
            logger.error("bootstrap administrator state failed: %s", type(exc).__name__)
            return _error(409, "state_conflict", "初始化状态冲突")

    return application
