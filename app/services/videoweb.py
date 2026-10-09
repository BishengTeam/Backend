"""Service-to-service integration with the closed course-video website."""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.adapter.redis import redis_client
from app.domain.certification.src.index import Course, CourseEnrollment
from app.domain.community.src.index import QuizCourseLibraryBinding
from app.domain.order.src.index import Order
from app.domain.user.src.index import User
from app.port.config import settings
from app.port.exceptions import RateLimitException, ThirdPartyException, UnauthorizedException

logger = logging.getLogger(__name__)


class VideoWebService:
    """HTTP client for VideoWeb internal APIs.

    Network failures are deliberately not raised into payment fulfilment:
    a closed video-site outage must never roll back a successful WeChat payment.
    """

    _issue_tasks: set[asyncio.Task[None]] = set()

    @staticmethod
    def _login_keys(
        *, login_code: str | None = None, user_id: int | None = None
    ) -> dict[str, str]:
        prefix = "videoweb:login"
        keys: dict[str, str] = {}
        if login_code is not None:
            keys.update(
                {
                    "code": f"{prefix}:code:{login_code}",
                    "user": f"{prefix}:user:{login_code}",
                    "attempts": f"{prefix}:attempts:{login_code}",
                }
            )
        if user_id is not None:
            keys.update(
                {
                    "owner": f"{prefix}:owner:{user_id}",
                    "cooldown": f"{prefix}:cooldown:{user_id}",
                    "lock": f"{prefix}:lock:{user_id}",
                }
            )
        return keys

    @staticmethod
    def _text(value: object | None) -> str:
        return value.decode() if isinstance(value, bytes) else str(value or "")

    @classmethod
    async def create_login_code(cls, user_id: int) -> str:
        """Create a short-lived, single-use website login code.

        The credential is never logged. Redis failures fail closed because this
        value authenticates a user on the independent website.
        """
        owner_keys = cls._login_keys(user_id=user_id)
        ttl = settings.VIDEOWEB_LOGIN_CODE_TTL_SECONDS
        if not await redis_client.set(
            owner_keys["lock"], "1", nx=True, ex=max(1, min(ttl, 5))
        ):
            raise RateLimitException("登录码正在生成，请稍后再试")
        try:
            if not await redis_client.set(
                owner_keys["cooldown"],
                "1",
                nx=True,
                ex=settings.VIDEOWEB_LOGIN_CODE_COOLDOWN_SECONDS,
            ):
                raise RateLimitException("登录码生成过于频繁，请稍后再试")

            previous = cls._text(await redis_client.get(owner_keys["owner"]))
            if previous:
                previous_keys = cls._login_keys(login_code=previous)
                await redis_client.delete(
                    previous_keys["code"],
                    previous_keys["user"],
                    previous_keys["attempts"],
                )

            for _ in range(20):
                login_code = f"{secrets.randbelow(1_000_000):06d}"
                code_keys = cls._login_keys(login_code=login_code)
                if await redis_client.set(
                    code_keys["code"], str(user_id), nx=True, ex=ttl
                ):
                    await redis_client.set(code_keys["user"], str(user_id), ex=ttl)
                    await redis_client.set(owner_keys["owner"], login_code, ex=ttl)
                    return login_code
            raise RateLimitException("登录码生成失败，请稍后再试")
        finally:
            await redis_client.delete(owner_keys["lock"])

    @classmethod
    async def exchange_login_code(cls, login_code: str) -> int:
        """Consume a login code once and return its mini-program user ID."""
        code_keys = cls._login_keys(login_code=login_code)
        attempts = await redis_client.incr(code_keys["attempts"])
        if attempts == 1:
            await redis_client.expire(
                code_keys["attempts"], settings.VIDEOWEB_LOGIN_CODE_TTL_SECONDS
            )
        if attempts > settings.VIDEOWEB_LOGIN_CODE_MAX_ATTEMPTS:
            owner = cls._text(await redis_client.get(code_keys["user"]))
            await redis_client.delete(
                code_keys["code"], code_keys["user"], code_keys["attempts"]
            )
            if owner.isdigit():
                await redis_client.delete(
                    cls._login_keys(user_id=int(owner))["owner"]
                )
            raise UnauthorizedException("登录码错误次数过多，请重新生成")

        if await redis_client.delete(code_keys["code"]) != 1:
            raise UnauthorizedException("登录码不存在、已过期或已使用")

        owner = cls._text(await redis_client.get(code_keys["user"]))
        await redis_client.delete(code_keys["user"], code_keys["attempts"])
        if not owner.isdigit():
            raise UnauthorizedException("登录码无效，请重新生成")
        await redis_client.delete(cls._login_keys(user_id=int(owner))["owner"])
        return int(owner)

    @staticmethod
    def _enabled() -> bool:
        return bool(settings.VIDEOWEB_BASE_URL and settings.VIDEOWEB_SERVICE_TOKEN)

    @classmethod
    async def _request(
        cls,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        if not cls._enabled():
            raise ThirdPartyException("课程视频站未配置")
        url = f"{settings.VIDEOWEB_BASE_URL.rstrip('/')}{path}"
        headers = {"X-Service-Token": settings.VIDEOWEB_SERVICE_TOKEN}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.request(
                    method, url, params=params, json=json, headers=headers
                )
        except httpx.HTTPError as exc:
            raise ThirdPartyException("课程视频站暂时不可用") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail") or response.json().get("message")
            except Exception:
                detail = ""
            raise ThirdPartyException(detail or "课程视频站接口调用失败")
        return response.json()

    @classmethod
    async def issue_code(
        cls,
        *,
        course_id: int,
        order_id: int | None,
        miniapp_user_id: int | None,
        created_by: str,
        note: str | None = None,
    ) -> Any:
        payload = {
            "course_id": course_id,
            "quantity": 1,
            "source_order_id": order_id,
            "created_by": created_by,
            "note": note,
        }
        if miniapp_user_id is not None:
            payload["owner_miniapp_user_id"] = miniapp_user_id
        return await cls._request("POST", "/internal/codes", json=payload)

    @classmethod
    async def list_codes(
        cls,
        *,
        course_id: int | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Any:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if course_id is not None:
            params["course_id"] = course_id
        if status:
            params["status"] = status
        return await cls._request("GET", "/internal/codes", params=params)

    @classmethod
    async def list_user_codes(cls, miniapp_user_id: int) -> Any:
        return await cls._request("GET", f"/internal/users/{miniapp_user_id}/codes")

    @classmethod
    async def change_code_status(
        cls,
        code_id: int,
        action: Literal["revoke", "refund"],
        reason: str | None = None,
    ) -> Any:
        return await cls._request(
            "POST",
            f"/internal/codes/{code_id}/{action}",
            json={"reason": reason},
        )

    @staticmethod
    async def resolve_order_course_id(db: AsyncSession, order: Order) -> int | None:
        """Map a paid miniapp order to the single course represented by a code."""
        if order.order_kind == "course":
            enrollment = await db.scalar(
                select(CourseEnrollment).where(CourseEnrollment.order_id == order.id)
            )
            return enrollment.course_id if enrollment else None

        if order.order_kind == "quiz_order":
            _, _, library_id_text = order.product_type.partition(":")
            try:
                library_id = int(library_id_text)
            except ValueError:
                return None
            return await db.scalar(
                select(Course.id)
                .join(
                    QuizCourseLibraryBinding,
                    QuizCourseLibraryBinding.course_id == Course.id,
                )
                .where(
                    QuizCourseLibraryBinding.library_id == library_id,
                    QuizCourseLibraryBinding.status == "active",
                    Course.status == "published",
                    Course.is_active.is_(True),
                )
                .order_by(QuizCourseLibraryBinding.id.asc())
                .limit(1)
            )
        return None

    @classmethod
    async def issue_code_for_order(cls, order_id: int) -> bool:
        if not cls._enabled():
            return False
        async with get_db_ctx() as db:
            order = await db.get(Order, order_id)
            if order is None or order.status not in {"paid", "completed"}:
                return False
            course_id = await cls.resolve_order_course_id(db, order)
            if course_id is None:
                logger.warning(
                    "VideoWeb code skipped: order=%s has no mapped published course", order_id
                )
                return False
            user = await db.get(User, order.user_id)
            if user is None or not user.is_active:
                logger.warning("VideoWeb code skipped: order=%s has no active user", order_id)
                return False
            await cls.issue_code(
                course_id=course_id,
                order_id=order.id,
                miniapp_user_id=user.id,
                created_by="miniapp-backend",
                note="订单支付完成自动生成",
            )
            logger.info("VideoWeb code issued: order=%s course=%s", order_id, course_id)
            return True

    @classmethod
    def schedule_issue_for_order(cls, order_id: int) -> bool:
        """Issue after the caller's transaction has had time to commit."""
        if not cls._enabled():
            return False

        async def _run() -> None:
            try:
                await asyncio.sleep(max(0.1, settings.VIDEOWEB_ISSUE_DELAY_SECONDS))
                await cls.issue_code_for_order(order_id)
            except Exception:
                # Payment remains successful; the reconciliation loop below
                # retries safely using VideoWeb's source_order_id idempotency.
                logger.exception("Failed to issue VideoWeb code: order=%s", order_id)

        task = asyncio.create_task(_run())
        cls._issue_tasks.add(task)
        task.add_done_callback(cls._issue_tasks.discard)
        return True


async def videoweb_code_reconciliation_worker_loop() -> None:
    """Retry recent paid order codes; VideoWeb deduplicates by order ID."""
    service = VideoWebService
    while True:
        try:
            if not service._enabled():
                return
            cutoff = datetime.now(timezone.utc) - timedelta(
                days=settings.VIDEOWEB_RECONCILE_WINDOW_DAYS
            )
            async with get_db_ctx() as db:
                order_ids = (
                    (
                        await db.execute(
                            select(Order.id)
                            .where(
                                Order.order_kind.in_(("course", "quiz_order")),
                                Order.status.in_(("paid", "completed")),
                                Order.updated_at >= cutoff,
                            )
                            .order_by(Order.id.asc())
                        )
                    )
                    .scalars()
                    .all()
                )
            for order_id in order_ids:
                try:
                    await service.issue_code_for_order(order_id)
                except Exception:
                    logger.exception(
                        "VideoWeb code reconciliation failed: order=%s", order_id
                    )
        except Exception:
            logger.exception("VideoWeb code reconciliation loop failed")
        await asyncio.sleep(max(30, settings.VIDEOWEB_RECONCILE_POLL_SECONDS))
