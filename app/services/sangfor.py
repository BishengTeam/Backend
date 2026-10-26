"""Batch-based Sangfor registration using the existing order and bank ledger.

Order.extra_data owns the typed, versioned application. No second payment or
refund state machine is introduced; financial decisions remain in OfflinePayment.
"""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from zoneinfo import ZoneInfo

from openpyxl import load_workbook
from sqlalchemy import and_, case, func, select
from sqlalchemy.exc import IntegrityError

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order, apply_order_status_transition
from app.domain.plan.src.index import Plan
from app.models.cert_product import CertProduct
from app.models.offline_payment import OfflinePaymentAudit
from app.port.config import settings
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.sangfor import SangforCreate, SangforDecision, SangforResubmit, SangforSubmission
from app.services.agreement_template import ensure_accepted
from app.services.certification_identity import lock_verified_identity
from app.services.offline_payment import OfflinePaymentService
from app.services.plan_enrollment import PlanEnrollmentService
from app.services.registration_payment_guard import payment_filter, payment_summary, require_review_payment
from app.services.sangfor_catalog import DIRECTIONS, HEADERS, require_direction
from app.utils.payment import generate_out_trade_no


def now():
    return datetime.now(timezone.utc)


def is_sangfor(order):
    return (order.extra_data or {}).get("business_type") == "sangfor"


def registration_status(order):
    if order.status == "closed":
        return "cancelled"
    if order.status == "refunded":
        return "refunded_closed"
    if order.status == "pending":
        return "pending_payment"
    return (order.extra_data or {}).get("registration_state", "pending_review")


def export_workbook(snapshots):
    from pathlib import Path
    template = Path(__file__).resolve().parents[1] / "assets" / "sangfor_registration.xlsx"
    workbook = load_workbook(template)
    sheet = workbook["Sheet1"]
    if [sheet.cell(2, column).value for column in range(1, 14)] != HEADERS:
        raise BusinessException("深信服报送模板表头不匹配，请联系管理员")
    # The source contains one yellow example row; replace it with actual records.
    for row in sheet.iter_rows(min_row=3, max_row=max(sheet.max_row, 3), max_col=13):
        for cell in row:
            cell.value = None
    for index, data in enumerate(snapshots, 3):
        date = datetime.fromisoformat(data["exam_date"]).astimezone(ZoneInfo(settings.APP_TIMEZONE)).replace(tzinfo=None)
        values = [data["name"], data["id_card"], data["phone"], date, data["mailing_address"], data["email"],
                  data["organization"], data["first_name"], data["last_name"], data["exam_direction"],
                  data["exam_voucher_code"], data["remark1"], data["remark2"]]
        for column, value in enumerate(values, 1):
            cell = sheet.cell(index, column, value)
            if column == 4:
                cell.number_format = "yyyy-mm-dd"
            elif isinstance(value, str):
                cell.data_type = "s"  # User text must never become an Excel formula.
                cell.number_format = "@"
    # Keep the supplied layout; remove conflicting, incomplete dropdown rules.
    sheet.data_validations.dataValidation = []
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


class SangforService:
    async def require_product(self, code):
        require_direction(code)
        async with get_db_ctx() as db:
            product = await db.scalar(select(CertProduct).where(CertProduct.code == code, CertProduct.type == "sangfor"))
            if product is None:
                raise NotFoundException("深信服认证产品")

    @staticmethod
    def query():
        return select(Order).where(Order.order_kind == "certification", Order.extra_data["business_type"].as_string() == "sangfor")

    async def batch(self, db, batch_id, *, public=False):
        pair = (await db.execute(select(Plan, CertProduct).join(CertProduct, CertProduct.code == Plan.product_type)
                                 .where(Plan.id == batch_id, CertProduct.type == "sangfor"))).first()
        if pair is None or (public and (not pair[1].is_active or pair[0].status not in {"published", "registration_closed"} or pair[1].code not in DIRECTIONS)):
            raise NotFoundException("深信服考试批次")
        return pair

    @staticmethod
    async def batch_response(db, plan, product):
        enrolled = await db.scalar(select(func.count()).select_from(Order).where(Order.plan_id == plan.id, Order.status.in_(("pending", "paid", "completed"))))
        return {"id": plan.id, "product_code": product.code, "product_name": product.chinese_name,
                "description": plan.description or product.description, "name": plan.name, "status": plan.status,
                "exam_direction": DIRECTIONS.get(product.code, product.code), "apply_start": plan.apply_start,
                "apply_end": plan.apply_end, "exam_date": plan.exam_date, "exam_location": plan.exam_location,
                "price_cents": plan.price_cents, "capacity": plan.capacity, "enrolled": enrolled,
                "remaining_count": max(0, plan.capacity - enrolled) if plan.capacity else -1}

    async def batches(self, *, public=False, product_code=None):
        async with get_db_ctx() as db:
            query = select(Plan, CertProduct).join(CertProduct, CertProduct.code == Plan.product_type).where(CertProduct.type == "sangfor")
            if public:
                query = query.where(CertProduct.is_active.is_(True), CertProduct.code.in_(DIRECTIONS), Plan.status.in_(("published", "registration_closed")))
            if product_code:
                query = query.where(Plan.product_type == product_code)
            pairs = (await db.execute(query.order_by(Plan.id.desc()))).all()
            return [await self.batch_response(db, plan, product) for plan, product in pairs]

    async def get_batch(self, batch_id, *, public=False):
        async with get_db_ctx() as db:
            return await self.batch_response(db, *(await self.batch(db, batch_id, public=public)))

    @staticmethod
    async def refund_approved(db, order_id):
        return bool(await db.scalar(select(OfflinePaymentAudit.id).where(OfflinePaymentAudit.order_id == order_id,
                              OfflinePaymentAudit.action == "admin.generic_refund_approved").limit(1)))

    async def response(self, db, order, *, brief=False):
        extra = order.extra_data
        payment = await payment_summary(db, order)
        status = registration_status(order)
        if order.status in {"paid", "completed"} and await self.refund_approved(db, order.id):
            status = "refund_processing"
            payment.review_payment_allowed = False
            payment.review_block_reason = "退款已审批，等待登记实际退款，报名已冻结"
        return {"id": order.id, "order_id": order.id, "batch_id": order.plan_id, "registration_no": order.out_trade_no,
                "status": status, "order_status": order.status, "price_cents": order.price,
                "revision": extra["revision"], "current_version": extra["current_version"],
                "candidate_snapshot": extra["candidate_snapshot"], "batch_snapshot": extra["batch_snapshot"],
                "versions": [] if brief else extra["versions"], "reviews": extra["reviews"][-1:] if brief else extra["reviews"], "exports": extra["exports"],
                "correction_due_at": extra.get("correction_due_at"), "payment_summary": payment.model_dump(),
                "created_at": order.created_at, "updated_at": order.updated_at}

    async def lock_order(self, db, order_id, user_id=None):
        query = self.query().where(Order.id == order_id)
        if user_id is not None:
            query = query.where(Order.user_id == user_id)
        order = await db.scalar(query.with_for_update())
        if order is None:
            raise NotFoundException("深信服报名")
        return order

    async def create(self, user_id, data: SangforCreate):
        try:
            async with get_db_ctx() as db:
                async with db.begin():
                    plan = await PlanEnrollmentService().lock_enrollable_plan(db, plan_id=data.batch_id, user_id=user_id, expected_vendor="sangfor")
                    direction = require_direction(plan.product_type)
                    if not plan.exam_date:
                        raise BusinessException("该批次未配置考试时间")
                    identity = await lock_verified_identity(db, user_id=user_id)
                    await ensure_accepted(db, user_id, "cert_registration", message="请先阅读并同意认证报名信息处理授权协议")
                    snapshot = data.model_dump(exclude={"batch_id"}) | {"name": identity.real_name, "id_card": identity.id_card_number,
                        "exam_direction": direction, "exam_direction_code": plan.product_type, "exam_date": plan.exam_date.isoformat()}
                    order = Order(user_id=user_id, order_kind="certification", product_type=plan.product_type, plan_id=plan.id,
                        candidate_name=identity.real_name, candidate_idcard=identity.id_card_number, candidate_phone=data.phone,
                        price=plan.price_cents, status="pending", out_trade_no=generate_out_trade_no("SF"),
                        extra_data={"business_type": "sangfor", "revision": 1, "current_version": 1,
                            "registration_state": "pending_review", "candidate_snapshot": snapshot,
                            "batch_snapshot": {"name": plan.name, "product_code": plan.product_type,
                                "exam_direction": direction, "exam_date": plan.exam_date.isoformat(), "exam_location": plan.exam_location},
                            "versions": [{"version": 1, "submitted_at": now().isoformat(), "snapshot": snapshot}], "reviews": [], "exports": []})
                    db.add(order)
                    await db.flush()
                    await OfflinePaymentService.initialize(db, order, plan)
                    await db.flush()
                    await db.refresh(order)
                    return await self.response(db, order)
        except IntegrityError as error:
            raise ConflictException("已存在该批次的有效报名，请在我的报名查看") from error

    async def detail(self, order_id, user_id=None):
        async with get_db_ctx() as db:
            return await self.response(db, await self.lock_order(db, order_id, user_id))

    async def registrations(self, *, user_id=None, batch_id=None, state=None, keyword=None, payment_state=None, page=1, page_size=20):
        async with get_db_ctx() as db:
            query = self.query()
            if user_id is not None:
                query = query.where(Order.user_id == user_id)
            if batch_id:
                await self.batch(db, batch_id)
                query = query.where(Order.plan_id == batch_id)
            if keyword:
                query = query.where(Order.candidate_name.ilike(f"%{keyword}%") | Order.candidate_phone.ilike(f"%{keyword}%") | Order.out_trade_no.ilike(f"%{keyword}%"))
            if payment_state:
                query = query.where(payment_filter(payment_state))
            if state:
                refund = select(OfflinePaymentAudit.id).where(OfflinePaymentAudit.order_id == Order.id,
                    OfflinePaymentAudit.action == "admin.generic_refund_approved").exists()
                derived_state = case((Order.status == "closed", "cancelled"), (Order.status == "refunded", "refunded_closed"),
                    (Order.status == "pending", "pending_payment"),
                    (and_(Order.status.in_(("paid", "completed")), refund), "refund_processing"),
                    else_=func.coalesce(Order.extra_data["registration_state"].as_string(), "pending_review"))
                query = query.where(derived_state == state)
            total = await db.scalar(select(func.count()).select_from(query.subquery()))
            rows = list((await db.scalars(query.order_by(Order.id.desc()).offset((page - 1) * page_size).limit(page_size))).all())
            return {"items": [await self.response(db, row, brief=True) for row in rows], "total": total, "page": page, "page_size": page_size}

    async def editable(self, db, order):
        await require_review_payment(db, order)
        if await self.refund_approved(db, order.id):
            raise ConflictException("退款已审批，不能继续报名或审核")
        plan, _product = await self.batch(db, order.plan_id)
        if plan.status in {"cancelled", "archived", "finalized"}:
            raise ConflictException("批次已终止，不能继续办理")

    @staticmethod
    def check_revision(order, expected):
        if order.extra_data["revision"] != expected:
            raise ConflictException("报名记录已更新，请刷新后重新操作")

    async def resubmit(self, order_id, user_id, data: SangforResubmit):
        async with get_db_ctx() as db:
            async with db.begin():
                order = await self.lock_order(db, order_id, user_id)
                self.check_revision(order, data.expected_revision)
                await self.editable(db, order)
                extra = deepcopy(order.extra_data)
                if registration_status(order) != "returned" or not extra.get("correction_due_at") or datetime.fromisoformat(extra["correction_due_at"]) < now():
                    raise ConflictException("当前不在有效补正期限内")
                identity = await lock_verified_identity(db, user_id=user_id)
                if identity.real_name != order.candidate_name or identity.id_card_number != order.candidate_idcard:
                    raise ConflictException("本人实名信息已变更，请联系管理员核实，不能替换报名人")
                snapshot = extra["candidate_snapshot"] | data.model_dump(exclude={"expected_revision"})
                extra["current_version"] += 1
                extra["revision"] += 1
                extra["registration_state"] = "pending_review"
                extra["candidate_snapshot"] = snapshot
                extra["correction_due_at"] = None
                extra["exported_version"] = None
                extra["versions"].append({"version": extra["current_version"], "submitted_at": now().isoformat(), "snapshot": snapshot})
                order.extra_data = extra
                order.candidate_phone = data.phone
                await db.flush()
                await db.refresh(order)
                return await self.response(db, order)

    async def review(self, order_id, admin_id, data: SangforDecision):
        async with get_db_ctx() as db:
            async with db.begin():
                order = await self.lock_order(db, order_id)
                self.check_revision(order, data.expected_revision)
                await self.editable(db, order)
                extra = deepcopy(order.extra_data)
                state = registration_status(order)
                if data.action in {"approve", "return"} and state != "pending_review":
                    raise ConflictException("仅待初审报名可执行该操作")
                if data.action.startswith("final_") and (state != "approved" or extra.get("exported_version") != extra["current_version"]):
                    raise ConflictException("当前版本须初审通过并已成功导出后才能登记外审结果")
                if data.action in {"return", "final_return"}:
                    if not data.reason or not data.correction_due_at or data.correction_due_at.tzinfo is None or data.correction_due_at <= now():
                        raise BusinessException("退回补正必须填写原因和未来的补正截止时间")
                    extra["registration_state"] = "returned"
                    extra["correction_due_at"] = data.correction_due_at.isoformat()
                else:
                    extra["registration_state"] = "approved" if data.action == "approve" else "final_approved"
                    if data.action == "final_approve":
                        apply_order_status_transition(order, "completed")
                extra["revision"] += 1
                extra["reviews"].append({"action": data.action, "reason": data.reason, "admin_id": admin_id,
                    "version": extra["current_version"], "created_at": now().isoformat(), "correction_due_at": extra.get("correction_due_at")})
                order.extra_data = extra
                await db.flush()
                await db.refresh(order)
                return await self.response(db, order)

    async def export(self, batch_id, ids, admin_id):
        async with get_db_ctx() as db:
            async with db.begin():
                await self.batch(db, batch_id)
                orders = list((await db.scalars(self.query().where(Order.plan_id == batch_id, Order.id.in_(ids)).order_by(Order.id).with_for_update())).all())
                if len(orders) != len(ids):
                    raise ConflictException("选择中包含不存在或属于其他批次的报名")
                snapshots = []
                for order in orders:
                    await self.editable(db, order)
                    if registration_status(order) not in {"approved", "final_approved"}:
                        raise ConflictException(f"{order.candidate_name}尚未初审通过，不能报送")
                    snapshot = order.extra_data["candidate_snapshot"]
                    SangforSubmission.model_validate({key: snapshot[key] for key in SangforSubmission.model_fields})
                    require_direction(snapshot["exam_direction_code"])
                    snapshots.append(snapshot)
                content = export_workbook(snapshots)
                checksum = sha256(content).hexdigest()
                for order in orders:
                    extra = deepcopy(order.extra_data)
                    extra["exported_version"] = extra["current_version"]
                    extra["revision"] += 1
                    extra["exports"].append({"version": extra["current_version"], "sha256": checksum, "admin_id": admin_id, "created_at": now().isoformat()})
                    order.extra_data = extra
                return content
