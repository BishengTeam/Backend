"""Bank transfers never use provider payment/refund identifiers.

All decisions serialize on Order, before business rows. The unique bank-entry
constraint is the cross-order arbiter; a failed claim rolls back fulfillment.
"""
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order, apply_order_status_transition, confirm_inventory_sale
from app.domain.plan.src.index import Plan
from app.integrations.renshe_storage import RensheObjectStorage
from app.models.offline_payment import (
    OfflineBankEntry, OfflinePayment, OfflinePaymentAudit, OfflinePaymentMaterial,
    OfflinePaymentSettings, OfflinePaymentSubmission,
)
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.port.config import settings
from app.schemas.offline_payment import SettingsInput
from app.services.order_fulfillment import OrderFulfillmentService

MAX_MATERIAL_BYTES = 10 * 1024 * 1024
STATUSES = {"awaiting_proof": "请转账并提交凭证", "under_review": "凭证已提交，等待核账",
            "returned": "请补充转账凭证", "exception": "请联系管理员核实",
            "confirmed": "付款已确认", "closed": "订单已关闭", "refunded": "款项已退回"}


def now():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def bank_reference(value):
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).upper()
    if not normalized or len(normalized) > 128 or any(unicodedata.category(c).startswith("C") for c in normalized):
        raise BusinessException("银行流水标识无效，请按银行记录核对")
    return normalized


def protected_payment(order):
    return getattr(order, "payment_channel", None) == "bank_transfer" and getattr(order, "offline_status", None) not in {None, "awaiting_proof", "closed"}


def assert_cancellable(order):
    if protected_payment(order):
        raise ConflictException("订单已申报转账，请联系管理员核实，不能直接取消或修改金额")


class OfflinePaymentService:
    @staticmethod
    async def initialize(db, order, plan=None):
        if order.price == 0:
            order.payment_channel = "free"
            return
        if not settings.BANK_TRANSFER_NEW_ORDERS_ENABLED:
            raise BusinessException("现金报名暂时暂停，已有订单仍可核账和退款")
        if settings.RENSHE_STORAGE_TYPE not in {"local", "aliyun_oss"}:
            raise BusinessException("私有转账凭证存储尚未启用，请联系管理员")
        config = await db.get(OfflinePaymentSettings, 1)
        if config is None:
            raise BusinessException("暂未配置银行收款账户，请联系管理员")
        bank_settings = SettingsInput.model_validate({**config.data, "version": config.version})
        snapshot = bank_settings.model_dump(exclude={"version"})
        if plan is None and order.plan_id:
            plan = await db.get(Plan, order.plan_id)
        if plan:
            snapshot["contact_name"] = plan.contact_name or snapshot["contact_name"]
            snapshot["contact_phone"] = plan.contact_phone or snapshot["contact_phone"]
        deadline = now() + timedelta(hours=bank_settings.submit_hours)
        if plan and plan.apply_end:
            deadline = min(deadline, aware(plan.apply_end))
        if deadline <= now():
            raise ConflictException("报名截止，不能创建付款订单")
        order.payment_channel = "bank_transfer"
        order.offline_status = "awaiting_proof"
        order.expires_at = deadline
        db.add(OfflinePayment(order_id=order.id, account_snapshot=snapshot,
            account_fingerprint=digest(bank_settings.account_number), deadline=deadline))

    @staticmethod
    async def lock(db, order_id, user_id=None):
        conditions = [Order.id == order_id]
        if user_id is not None:
            conditions.append(Order.user_id == user_id)
        order = await db.scalar(select(Order).where(*conditions).with_for_update())
        if order is None:
            raise NotFoundException("订单")
        if order.payment_channel != "bank_transfer":
            raise ConflictException("该订单不是银行转账订单，请联系管理员处理原支付渠道")
        payment = await db.scalar(select(OfflinePayment).where(OfflinePayment.order_id == order.id))
        if payment is None:
            raise ConflictException("订单缺少收款信息快照")
        return order, payment

    @staticmethod
    async def command(db, order, actor_id, action, data):
        payload = data.model_dump(mode="json")
        existing = await db.scalar(select(OfflinePaymentAudit).where(
            OfflinePaymentAudit.order_id == order.id, OfflinePaymentAudit.action == action,
            OfflinePaymentAudit.request_key == data.request_key))
        if existing:
            if existing.payload_hash != digest(payload) or existing.actor_id != actor_id:
                raise ConflictException("请求标识已使用，请刷新后重试")
            return False
        audit = OfflinePaymentAudit(order_id=order.id, actor_id=actor_id, action=action,
            request_key=data.request_key, payload_hash=digest(payload), summary={
                "version": data.expected_version, "expected_version": data.expected_version, "note": data.note,
                "expected_amount_cents": order.price,
                "amount_cents": getattr(data, "amount_cents", None),
                "bank_reference": payload.get("bank_reference"), "bank_time": payload.get("bank_time"),
                "deadline": payload.get("deadline")})
        db.add(audit)
        return audit

    @staticmethod
    def version(payment, data):
        if payment.current_version != data.expected_version:
            raise ConflictException("凭证已更新，请刷新并核对最新版本")

    async def detail(self, order_id, user_id=None):
        async with get_db_ctx() as db:
            order = await db.get(Order, order_id)
            if order is None or (user_id is not None and order.user_id != user_id):
                raise NotFoundException("订单")
            if order.payment_channel != "bank_transfer":
                raise ConflictException("历史订单请联系管理员核实原支付渠道")
            payment = await db.scalar(select(OfflinePayment).where(OfflinePayment.order_id == order_id))
            if payment is None:
                raise NotFoundException("收款记录")
            submissions = list((await db.scalars(select(OfflinePaymentSubmission).where(
                OfflinePaymentSubmission.order_id == order_id).order_by(OfflinePaymentSubmission.version.desc()))).all())
            materials = list((await db.scalars(select(OfflinePaymentMaterial).where(
                OfflinePaymentMaterial.order_id == order_id))).all())
            entries = list((await db.scalars(select(OfflineBankEntry).where(OfflineBankEntry.order_id == order_id))).all())
            audits = list((await db.scalars(select(OfflinePaymentAudit).where(
                OfflinePaymentAudit.order_id == order_id).order_by(OfflinePaymentAudit.id))).all()) if user_id is None else []
            from app.domain.h3c.src.index import H3cRegistration
            from app.domain.nisp.src.index import NispRegistration
            business_link = {"vendor": "renshe", "registration_id": order.application_id, "batch_id": order.plan_id} if order.application_id else None
            if business_link is None:
                for vendor, model in [("h3c", H3cRegistration), ("nisp", NispRegistration)]:
                    registration = await db.scalar(select(model).where(model.order_id == order_id))
                    if registration:
                        business_link = {"vendor": vendor, "registration_id": registration.id, "batch_id": registration.batch_id}
                        break
            business = business_link["vendor"] if business_link else "order"
            if (order.extra_data or {}).get("business_type") == "sangfor":
                business_link = {"vendor": "sangfor", "registration_id": order.id, "batch_id": order.plan_id}
                # Sangfor reuses the existing generic bank refund approval and ledger.
                business = "order"
            status = "refunded" if order.status == "refunded" else "closed" if order.status == "closed" and order.offline_status != "exception" else order.offline_status
            return {"order_id": order_id, "order_status": order.status, "out_trade_no": order.out_trade_no,
                "product_type": order.product_type, "amount_cents": order.price,
                "original_price": order.original_price, "coupon_code": order.coupon_code,
                "status": status, "status_label": "请确认零元报名" if order.price == 0 and order.status == "pending" and payment.current_version == 0 else STATUSES.get(status, status),
                "account": payment.account_snapshot, "current_version": payment.current_version,
                "deadline": payment.deadline, "message": payment.message,
                "confirmed_at": payment.confirmed_at,
                "refund_business": business,
                "business_link": business_link if user_id is None else None,
                "generic_refund_approved": any(a.action == "admin.generic_refund_approved" for a in audits),
                "can_submit": order.status == "pending" and order.offline_status in {"awaiting_proof", "returned"} and aware(payment.deadline) > now(),
                "can_cancel": order.status == "pending" and order.offline_status == "awaiting_proof",
                "can_change_coupon": order.status == "pending" and payment.current_version == 0 and order.offline_status == "awaiting_proof" and aware(payment.deadline) > now(),
                "can_report_exception": not any(e.kind == "refund" for e in entries) and (order.status == "closed" or (order.status == "pending" and (order.offline_status == "exception" or (order.offline_status in {"awaiting_proof", "returned"} and aware(payment.deadline) <= now())))),
                "submissions": [{"version": s.version, "status": s.status, "reason": s.reason,
                    "created_at": s.created_at, **s.data} for s in submissions],
                "materials": [{"id": m.id, "filename": m.filename, "size_bytes": m.size_bytes,
                    "content_type": m.content_type} for m in materials if user_id is None or m.user_id == user_id],
                "entries": [{"kind": e.kind, "amount_cents": e.amount_cents,
                    "bank_time": e.bank_time, "created_at": e.created_at,
                    "material_ids": e.material_ids if user_id is None else [],
                    "bank_reference": e.bank_reference if user_id is None else None} for e in entries],
                "audits": [{"action": a.action, "actor_id": a.actor_id, "created_at": a.created_at,
                    "summary": a.summary} for a in audits]}

    async def upload(self, order_id, file, user_id=None, admin_id=None):
        # Authenticate ownership before crossing storage. Recheck on submission.
        async with get_db_ctx() as db:
            order, _ = await self.lock(db, order_id, user_id)
            if user_id is not None and not (order.status in {"pending", "closed"}):
                raise ConflictException("当前订单不能上传付款凭证")
            if user_id is not None and order.status == "pending" and order.offline_status not in {"awaiting_proof", "returned", "exception"}:
                raise ConflictException("凭证正在核账，请等待处理结果")
            if user_id is not None and await db.scalar(select(OfflineBankEntry.id).where(
                OfflineBankEntry.order_id == order.id, OfflineBankEntry.kind == "refund")):
                raise ConflictException("转账已实际退回，不能继续上传凭证")
        data = await file.read(MAX_MATERIAL_BYTES + 1)
        filename = PurePath((file.filename or "凭证").replace("\\", "/")).name[:240]
        extension = PurePath(filename).suffix.lower()
        types = {".pdf": ("application/pdf", b"%PDF-"), ".jpg": ("image/jpeg", b"\xff\xd8\xff"),
                 ".jpeg": ("image/jpeg", b"\xff\xd8\xff"), ".png": ("image/png", b"\x89PNG\r\n\x1a\n")}
        if extension not in types or not data or len(data) > MAX_MATERIAL_BYTES:
            raise BusinessException("凭证支持 JPG、PNG、PDF，每个文件不超过 10 MB")
        content_type, header = types[extension]
        if not data.startswith(header):
            raise BusinessException("文件内容与格式不符")
        prefix = settings.ALIYUN_OSS_PREFIX.strip("/") or "renshe"
        key = f"{prefix}/offline-payments/{order_id}/{uuid4().hex}{extension}"
        try:
            await RensheObjectStorage()._put(key, data, content_type)
        except OSError as exc:
            import logging
            logging.getLogger(__name__).exception("Offline payment storage unavailable: order_id=%s", order_id)
            raise BusinessException("凭证存储暂不可用，请稍后重试") from exc
        async with get_db_ctx() as db:
            asset = OfflinePaymentMaterial(order_id=order_id, user_id=user_id, admin_id=admin_id,
                storage_key=key, filename=filename, content_type=content_type,
                size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            db.add(asset)
            await db.commit()
            return {"id": asset.id, "filename": asset.filename, "size_bytes": asset.size_bytes, "content_type": asset.content_type}

    async def content(self, order_id, material_id, user_id=None):
        async with get_db_ctx() as db:
            order = await db.get(Order, order_id)
            if order is None or (user_id is not None and order.user_id != user_id):
                raise NotFoundException("订单")
            if order.payment_channel != "bank_transfer":
                raise ConflictException("该订单不是银行转账订单")
            asset = await db.get(OfflinePaymentMaterial, material_id)
            if asset is None or asset.order_id != order_id or (user_id is not None and asset.user_id != user_id):
                raise NotFoundException("转账凭证")
            key, content_type, filename = asset.storage_key, asset.content_type, asset.filename
        content, _ = await RensheObjectStorage()._get(key)
        return content, content_type, filename

    @staticmethod
    async def check_materials(db, order, material_ids, user_id=None, admin_id=None):
        rows = list((await db.scalars(select(OfflinePaymentMaterial).where(
            OfflinePaymentMaterial.id.in_(material_ids), OfflinePaymentMaterial.order_id == order.id))).all())
        if len(rows) != len(set(material_ids)) or len(rows) != len(material_ids) or not 1 <= len(rows) <= 3:
            raise ConflictException("凭证附件无效或重复")
        if any((user_id is not None and m.user_id != user_id) or
               (admin_id is not None and m.admin_id != admin_id) for m in rows):
            raise ConflictException("凭证附件不属于当前提交人")

    async def submit(self, order_id, user_id, data, exception=False):
        async with get_db_ctx() as db:
            async with db.begin():
                order, payment = await self.lock(db, order_id, user_id)
                audit = await self.command(db, order, user_id, "user.exception" if exception else "user.submit", data)
                if not audit:
                    return {"status": order.offline_status}
                self.version(payment, data)
                if exception:
                    late = order.offline_status in {"awaiting_proof", "returned"} and aware(payment.deadline) <= now()
                    if order.status not in {"pending", "closed"} or (order.status != "closed" and order.offline_status != "exception" and not late):
                        raise ConflictException("请使用正常凭证提交入口")
                    if await db.scalar(select(OfflineBankEntry.id).where(OfflineBankEntry.order_id == order.id, OfflineBankEntry.kind == "refund")):
                        raise ConflictException("异常转账已实际退回，不能重复申报")
                elif order.status != "pending" or order.offline_status not in {"awaiting_proof", "returned"}:
                    raise ConflictException("当前状态不能提交凭证")
                elif order.price == 0:
                    raise ConflictException("零元订单无需转账，请确认零元报名")
                elif aware(payment.deadline) <= now():
                    raise ConflictException("凭证提交期限已过，请联系管理员核实转账")
                await self.check_materials(db, order, data.material_ids, user_id=user_id)
                payment.current_version += 1
                audit.summary = {**audit.summary, "version": payment.current_version}
                db.add(OfflinePaymentSubmission(order_id=order.id, version=payment.current_version,
                    data=data.model_dump(mode="json", exclude={"expected_version", "request_key"}),
                    status="exception" if exception else "under_review"))
                order.offline_status = "exception" if exception else "under_review"
                order.offline_submitted_at = now()
                order.expires_at = None
                payment.message = "已申报转账，等待管理员核实" if exception else None
                return {"status": order.offline_status, "version": payment.current_version}

    async def decide(self, order_id, admin_id, data, action):
        try:
            async with get_db_ctx() as db:
                async with db.begin():
                    order, payment = await self.lock(db, order_id)
                    audit = await self.command(db, order, admin_id, f"admin.{action}", data)
                    if not audit:
                        return {"status": order.offline_status, "replayed": True}
                    self.version(payment, data)
                    if action == "exception" and (order.status in {"paid", "completed"} or (order.status == "closed" and order.offline_status == "exception")):
                        order.offline_status = "exception"
                        payment.message = data.note
                        audit.summary = {**audit.summary, "result": "exception",
                            "reason_code": "manual_exception", "reason": data.note}
                        return {"status": order.offline_status}
                    if order.status != "pending" or order.offline_status not in {"under_review", "returned", "exception"}:
                        raise ConflictException("当前订单不能核账；已关闭订单需按资金异常人工处理")
                    submission = await db.scalar(select(OfflinePaymentSubmission).where(
                        OfflinePaymentSubmission.order_id == order.id, OfflinePaymentSubmission.version == payment.current_version))
                    if action == "confirm" and submission is None:
                        raise ConflictException("订单尚未提交转账凭证")
                    if action == "confirm" and order.offline_status == "returned":
                        raise ConflictException("凭证已驳回，请等待补交后核实新版本")
                    if action == "confirm" and order.plan_id:
                        plan = await db.get(Plan, order.plan_id)
                        if plan is None or plan.status in {"cancelled", "archived", "finalized"}:
                            raise ConflictException("批次已终止，请先处理资金异常或线下退款")
                    if action == "confirm" and data.amount_cents == order.price:
                        db.add(OfflineBankEntry(order_id=order.id, kind="receipt",
                            account_fingerprint=payment.account_fingerprint,
                            bank_reference=bank_reference(data.bank_reference), amount_cents=data.amount_cents,
                            bank_time=data.bank_time, admin_id=admin_id, submission_version=payment.current_version))
                        await db.flush()  # Claim the unique bank entry before any fulfillment.
                        apply_order_status_transition(order, "paid")
                        order.paid_at = now()
                        order.offline_status = "confirmed"
                        payment.confirmed_at = now()
                        payment.confirmed_by = admin_id
                        submission.status = "confirmed"
                        await confirm_inventory_sale(db, order, reason="bank_transfer_confirmed")
                        await OrderFulfillmentService().on_paid(db, order)
                    elif action == "return":
                        if submission is None:
                            raise ConflictException("没有可驳回的凭证")
                        deadline = data.deadline or now() + timedelta(hours=payment.account_snapshot["correction_hours"])
                        if deadline <= now():
                            raise ConflictException("补交截止时间必须晚于当前时间")
                        payment.deadline = deadline
                        audit.summary = {**audit.summary, "deadline": deadline.isoformat()}
                        order.offline_status = "returned"
                        submission.status = "returned"
                    else:
                        order.offline_status = "exception"
                        if submission:
                            submission.status = "exception"
                    payment.message = ("实际到账金额与应付金额不一致；" if action == "confirm" and data.amount_cents != order.price else "") + data.note
                    payment.message = payment.message[:1000]
                    if submission:
                        submission.reason = payment.message
                    audit.summary = {**audit.summary, "result": order.offline_status,
                        "reason_code": "amount_mismatch" if action == "confirm" and data.amount_cents != order.price
                            else "returned_for_correction" if action == "return"
                            else "manual_exception" if action == "exception" else None,
                        "reason": "实际到账金额与应付金额不一致" if action == "confirm" and data.amount_cents != order.price
                            else data.note if order.offline_status in {"exception", "returned"} else None}
                    order.expires_at = None
                    return {"status": order.offline_status}
        except IntegrityError as exc:
            raise ConflictException("银行流水已被其他订单认领，或该订单已核账，请刷新核对") from exc

    async def get_settings(self):
        async with get_db_ctx() as db:
            row = await db.get(OfflinePaymentSettings, 1)
            return {**row.data, "version": row.version} if row else None

    async def approve_generic_refund(self, order_id, admin_id, data):
        from app.services.admin_order import AdminOrderService
        async with get_db_ctx() as db:
            async with db.begin():
                order, payment = await self.lock(db, order_id)
                await AdminOrderService._reject_dedicated_certification_order(db, order)
                self.version(payment, data)
                if order.status not in {"paid", "completed"}:
                    raise ConflictException("订单不是可退款状态")
                await self.command(db, order, admin_id, "admin.generic_refund_approved", data)
                return {"status": "approved"}

    async def resolve_exception(self, order_id, admin_id, data):
        """Record an actual return of unallocated funds, never restore a closed seat."""
        from app.domain.order.src.index import release_inventory_lock
        from app.services.order import OrderService
        try:
            async with get_db_ctx() as db:
                async with db.begin():
                    order, payment = await self.lock(db, order_id)
                    if not await self.command(db, order, admin_id, "admin.exception_refund", data):
                        return {"status": order.offline_status, "replayed": True}
                    self.version(payment, data)
                    if order.status not in {"pending", "closed"} or order.offline_status != "exception":
                        raise ConflictException("仅未确认收款的资金异常可使用此入口；已收款订单须走专用退款审批")
                    if data.refund_bank_time < data.bank_time:
                        raise ConflictException("退款时间不能早于到账时间")
                    await self.check_materials(db, order, data.material_ids, admin_id=admin_id)
                    for kind, reference, time in [("receipt", data.bank_reference, data.bank_time),
                                                  ("refund", data.refund_bank_reference, data.refund_bank_time)]:
                        db.add(OfflineBankEntry(order_id=order.id, kind=kind,
                            account_fingerprint=payment.account_fingerprint, bank_reference=bank_reference(reference),
                            amount_cents=data.amount_cents, bank_time=time, admin_id=admin_id,
                            submission_version=payment.current_version,
                            material_ids=data.material_ids if kind == "refund" else []))
                    await db.flush()
                    if order.status == "pending":
                        apply_order_status_transition(order, "closed")
                        order.closed_at = now()
                        order.close_reason = "offline_exception_refunded"
                        await release_inventory_lock(db, order, reason=order.close_reason)
                        await OrderService._release_coupon(db, order)
                        await OrderFulfillmentService().on_closed(db, order)
                    order.offline_status = "closed"
                    order.expires_at = None
                    payment.message = f"异常转账已实际退回；{data.note}"[:1000]
                    return {"status": "closed"}
        except IntegrityError as exc:
            raise ConflictException("到账或退款流水已认领，不能重复登记") from exc

    async def settle_free(self, order_id, user_id):
        async with get_db_ctx() as db:
            async with db.begin():
                order, payment = await self.lock(db, order_id, user_id)
                if order.price != 0 or order.status != "pending" or payment.current_version:
                    raise ConflictException("当前订单不满足零元开通条件")
                if aware(payment.deadline) <= now():
                    raise ConflictException("订单已过期，请重新报名")
                if order.plan_id:
                    plan = await db.get(Plan, order.plan_id)
                    if plan is None or plan.status in {"cancelled", "archived", "finalized"}:
                        raise ConflictException("批次已终止，不能确认报名")
                apply_order_status_transition(order, "paid")
                order.payment_channel = "free"
                order.offline_status = None
                order.paid_at = now()
                order.expires_at = None
                await confirm_inventory_sale(db, order, reason="zero_price_coupon")
                await OrderFulfillmentService().on_paid(db, order)
                return {"status": order.status}

    async def refund(self, order_id, admin_id, data):
        from app.domain.h3c.src.index import H3cRefundRequest, H3cRegistration
        from app.domain.nisp.src.index import NispRefundRequest, NispRegistration
        from app.domain.renshe.src.index import RensheApplication, RensheRefundRequest
        from app.domain.order.src.index import refund_inventory_sale
        from app.services.renshe_refund import RensheRefundService

        models = {"h3c": H3cRefundRequest, "nisp": NispRefundRequest, "renshe": RensheRefundRequest}
        try:
            async with get_db_ctx() as db:
                async with db.begin():
                    order, payment = await self.lock(db, order_id)
                    if not await self.command(db, order, admin_id, "admin.refund", data):
                        return {"status": order.status, "replayed": True}
                    self.version(payment, data)
                    if order.status not in {"paid", "completed"} or data.amount_cents != order.price:
                        raise ConflictException("实际退款金额与已付款订单不一致")
                    if data.refund_kind == "order":
                        from app.services.admin_order import AdminOrderService
                        await AdminOrderService._reject_dedicated_certification_order(db, order)
                        approval = await db.scalar(select(OfflinePaymentAudit.id).where(
                            OfflinePaymentAudit.order_id == order.id, OfflinePaymentAudit.action == "admin.generic_refund_approved"))
                        if approval is None or data.refund_id != order.id:
                            raise ConflictException("请先审批该订单的退款")
                        await self.check_materials(db, order, data.material_ids, admin_id=admin_id)
                        db.add(OfflineBankEntry(order_id=order.id, kind="refund", account_fingerprint=payment.account_fingerprint,
                            bank_reference=bank_reference(data.bank_reference), amount_cents=data.amount_cents,
                            bank_time=data.bank_time, admin_id=admin_id, submission_version=payment.current_version, material_ids=data.material_ids))
                        await db.flush()
                        await refund_inventory_sale(db, order, reason="offline_refund_succeeded")
                        await OrderFulfillmentService().on_refunded(db, order)
                        apply_order_status_transition(order, "refunded")
                        order.offline_status = "refunded"
                        return {"status": order.status}
                    refund = await db.scalar(select(models[data.refund_kind]).where(
                        models[data.refund_kind].id == data.refund_id,
                        models[data.refund_kind].order_id == order.id))
                    if refund is None or refund.status != "approved" or refund.amount_cents != order.price:
                        raise ConflictException("退款尚未审批通过，请先使用该认证的退款审批流程")
                    business_model = RensheApplication if data.refund_kind == "renshe" else H3cRegistration if data.refund_kind == "h3c" else NispRegistration
                    business_id = refund.application_id if data.refund_kind == "renshe" else refund.registration_id
                    await db.scalar(select(business_model).where(business_model.id == business_id).with_for_update())
                    refund = await db.scalar(select(models[data.refund_kind]).where(
                        models[data.refund_kind].id == data.refund_id).with_for_update().execution_options(populate_existing=True))
                    if refund.status != "approved":
                        raise ConflictException("退款状态已变化，请刷新核对")
                    await self.check_materials(db, order, data.material_ids, admin_id=admin_id)
                    db.add(OfflineBankEntry(order_id=order.id, kind="refund",
                        account_fingerprint=payment.account_fingerprint, bank_reference=bank_reference(data.bank_reference),
                        amount_cents=data.amount_cents, bank_time=data.bank_time, admin_id=admin_id,
                        submission_version=payment.current_version, material_ids=data.material_ids))
                    await db.flush()
                    # The same terminal transitions as each dedicated refund workflow.
                    if data.refund_kind == "renshe":
                        application = await db.scalar(select(RensheApplication).where(
                            RensheApplication.id == refund.application_id).with_for_update())
                        if application is None or application.user_id != order.user_id or order.application_id != application.id:
                            raise ConflictException("退款报名归属不一致")
                        await OrderFulfillmentService().on_refunded(db, order)
                    else:
                        model = H3cRegistration if data.refund_kind == "h3c" else NispRegistration
                        registration = await db.scalar(select(model).where(model.id == refund.registration_id).with_for_update())
                        if registration is None or registration.order_id != order.id:
                            raise ConflictException("退款报名归属不一致")
                        registration.status = "refunded_closed"
                        registration.closed_at = now()
                        registration.close_reason = "offline_refund_succeeded"
                        await refund_inventory_sale(db, order, reason="offline_refund_succeeded")
                    apply_order_status_transition(order, "refunded")
                    order.offline_status = "refunded"
                    refund.status = "succeeded"
                    refund.succeeded_at = now()
                    refund.last_error = None
                    await db.flush()
                    if data.refund_kind == "renshe":
                        await RensheRefundService()._rebase_cleanup_if_refunds_resolved(
                            db, plan_id=application.plan_id, resolved_at=now())
                    return {"status": order.status}
        except IntegrityError as exc:
            raise ConflictException("退款流水或订单已登记，请刷新核对") from exc

    async def save_settings(self, admin_id, data):
        async with get_db_ctx() as db:
            async with db.begin():
                # Serialize even the first creation without upserting over concurrent edits.
                from sqlalchemy import text
                await db.execute(text("SELECT pg_advisory_xact_lock(80321001)"))
                row = await db.get(OfflinePaymentSettings, 1)
                if data.version != (row.version if row else 0):
                    raise ConflictException("收款设置已更新，请刷新后重试")
                account_fields = ("account_name", "bank_name", "account_number", "transfer_note", "submit_hours", "correction_hours")
                previous_account = {key: row.data.get(key) for key in account_fields} if row else None
                snapshot = data.model_dump(exclude={"version"})
                if row:
                    row.data = snapshot
                    row.version += 1
                else:
                    row = OfflinePaymentSettings(id=1, data=snapshot, version=1)
                    db.add(row)
                db.add(OfflinePaymentAudit(order_id=None, actor_id=admin_id, action="settings.update",
                    request_key=uuid4().hex, payload_hash=digest(snapshot), summary={"version": row.version,
                        "account_fingerprint": digest([data.bank_name, data.account_number]),
                        "previous_account": previous_account,
                        "account": {key: snapshot.get(key) for key in account_fields}}))
        return {**snapshot, "version": row.version}
