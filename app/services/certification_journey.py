"""Shared presentation and notice snapshots; dedicated registration rules stay authoritative."""
import hashlib
import json
from datetime import datetime, timezone

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.adapter.database import get_db_ctx
from app.domain.plan.src.index import Plan, PlanProduct
from app.domain.order.src.index import Order
from app.domain.h3c.src.index import H3cExamBatch
from app.domain.nisp.src.index import NispExamBatch
from app.models.cert_product import CertProduct
from app.models.certification_journey import CertificationJourneyContent
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.certification_journey import JourneyContent

LABELS = {"h3c": "H3C认证", "nisp": "NISP认证", "renshe": "人社认证", "sangfor": "深信服认证"}
DEFAULT_TEXT = {
    "h3c": "请使用本人实名资料报名，并根据所选科目及报名方式准备材料。学生报名须选择学信网验证码或学院盖章学籍证明核验方式；考券报名须提供考券号及证明。",
    "nisp": "请核对报考级别并使用本人实名资料报名。准备身份证双面PDF、二寸蓝底JPG照片；二级另需学籍报告PDF及二级申请表PDF。",
    "renshe": "请按所选职业、等级及申报类型如实填写资格信息，核对对应材料。承诺书须本人亲笔签名并按手印后上传。资格由管理员审核。",
    "sangfor": "请核对报考方向、姓名、身份证及拼音信息，报名时必须填写有效券码。请按批次安排参加考试。",
}


def utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def content_scope(vendor, plan_id=None):
    return f"{vendor}:{plan_id}" if plan_id else vendor


def default_content(vendor, *, batch=False):
    return JourneyContent(blocks=[] if batch else [
        {"type": "text", "text": DEFAULT_TEXT[vendor]},
        {"type": "text", "text": "提交报名后，请按订单要求完成线下转账并申报凭证；核账通过后进入报名审核。报名须知确认不替代信息处理授权协议。"},
    ]).model_dump()


def notice_digest(snapshot):
    return hashlib.sha256(json.dumps(jsonable_encoder(snapshot), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def content_record(db, vendor, plan_id=None, *, lock=False):
    query = select(CertificationJourneyContent).where(CertificationJourneyContent.scope == content_scope(vendor, plan_id))
    return await db.scalar(query.with_for_update() if lock else query)


class CertificationJourneyService:
    async def catalog(self, vendor, *, admin=False):
        async with get_db_ctx() as db:
            return await self.catalog_in_db(db, vendor, admin=admin)

    async def catalog_in_db(self, db, vendor, *, admin=False):
        products = list((await db.scalars(select(CertProduct).where(CertProduct.type == vendor).order_by(CertProduct.sort_order.desc(), CertProduct.id))).all())
        if not admin:
            products = [item for item in products if item.is_active and (vendor != "renshe" or item.renshe_status == "available")]
        product_ids = [item.id for item in products]
        product_by_id = {item.id: item for item in products}
        # v2: 通过 plan_product 关联查询批次
        rows = list((await db.execute(
            select(Plan, PlanProduct)
            .join(PlanProduct, PlanProduct.plan_id == Plan.id)
            .where(PlanProduct.product_id.in_(product_ids))
            .order_by(Plan.sort_order.desc(), Plan.id.desc())
        )).all()) if product_ids else []
        plans = [(plan, pp, product_by_id[pp.product_id]) for plan, pp in rows]
        if not admin:
            plans = [(plan, pp, prod) for plan, pp, prod in plans if plan.status in {"published", "registration_closed", "finalized"}]
        pp_ids = [pp.id for _, pp, _ in plans]
        # 容量按 plan_product 维度统计
        occupied_rows = (await db.execute(
            select(Order.plan_id, Order.product_type, func.count())
            .where(Order.plan_id.in_([plan.id for plan, _, _ in plans]), Order.status.in_(("pending", "paid", "completed")))
            .group_by(Order.plan_id, Order.product_type)
        )).all() if plans else []
        occupied = {(pid, code): count for pid, code, count in occupied_rows}
        h3c = {item.plan_product_id: item for item in (await db.scalars(select(H3cExamBatch).where(H3cExamBatch.plan_product_id.in_(pp_ids)))).all()} if vendor == "h3c" and pp_ids else {}
        nisp = {item.plan_product_id: item for item in (await db.scalars(select(NispExamBatch).where(NispExamBatch.plan_product_id.in_(pp_ids)))).all()} if vendor == "nisp" and pp_ids else {}
        result = []
        for plan, pp, prod in plans:
            batch = h3c.get(pp.id) if vendor == "h3c" else nisp.get(pp.id) if vendor == "nisp" else None
            if vendor in {"h3c", "nisp"} and batch is None:
                continue
            if vendor == "sangfor":
                from app.services.sangfor_catalog import DIRECTIONS
                if not admin and prod.code not in DIRECTIONS:
                    continue
            enrolled = occupied.get((plan.id, prod.code), 0)
            remaining = max(0, pp.capacity - enrolled) if pp.capacity else -1
            now, start, end = datetime.now(timezone.utc), utc(plan.apply_start), utc(plan.apply_end)
            supported = True
            if vendor == "renshe":
                config = {}  # v2: renshe_config 从 renshe_project_config 关联获取
                supported = config.get("qualification_rule") in {"related_major_graduated_degree_v1", "documented_employment_v1", "level_five_employment_v1", "level_three_v1"} and config.get("reporting_policy", {}).get("contract_version") == 2
            prices = [{"value": "coupon", "label": "考券报名", "price_cents": batch.coupon_price_cents}, {"value": "student", "label": "学生报名", "price_cents": batch.student_price_cents}, {"value": "full", "label": "全额报名", "price_cents": batch.full_price_cents}] if vendor == "h3c" else [{"value": batch.level if vendor == "nisp" else "default", "label": f"NISP{batch.level}级" if vendor == "nisp" else plan.application_type if vendor == "renshe" else "报名费", "price_cents": (batch.level1_price_cents if batch.level == "1" else batch.level2_price_cents) if vendor == "nisp" else plan.price_cents}]
            result.append({"id": batch.id if batch else plan.id, "plan_id": plan.id, "plan_product_id": pp.id, "subject_id": prod.code, "name": plan.name,
                "status": plan.status, "apply_start": plan.apply_start, "apply_end": plan.apply_end, "exam_date": plan.exam_date,
                "exam_location": plan.exam_location, "remaining_count": remaining, "capacity": pp.capacity,
                "can_apply": bool(supported and plan.status == "published" and start and end and start <= now < end and remaining != 0),
                "level": batch.level if vendor == "nisp" else None, "skill_level": pp.skill_level if vendor == "renshe" else None,
                "application_type": pp.application_type if vendor == "renshe" else None, "prices": prices})
        return {"vendor": vendor, "title": LABELS[vendor], "introduction": DEFAULT_TEXT[vendor],
            "subjects": [{"id": item.code, "name": item.chinese_name or item.name, "description": item.description} for item in products], "batches": jsonable_encoder(result)}

    async def get_content(self, vendor, plan_id=None):
        async with get_db_ctx() as db:
            await self._check_plan(db, vendor, plan_id)
            row = await content_record(db, vendor, plan_id)
            return {**(row.content if row else default_content(vendor, batch=bool(plan_id))), "version": row.version if row else 0}

    @staticmethod
    async def _check_plan(db, vendor, plan_id):
        if plan_id is not None:
            plan = await db.get(Plan, plan_id)
            product = await db.scalar(select(CertProduct).where(CertProduct.code == plan.product_type, CertProduct.type == vendor)) if plan else None
            if product is None:
                raise NotFoundException("该认证的批次")

    async def update_content(self, vendor, data, admin_id, plan_id=None):
        try:
            async with get_db_ctx() as db:
                async with db.begin():
                    await self._check_plan(db, vendor, plan_id)
                    # Serialize creation and updates across the same vendor, including empty scopes.
                    await db.execute(select(CertProduct.id).where(CertProduct.type == vendor).order_by(CertProduct.id).with_for_update())
                    row = await content_record(db, vendor, plan_id, lock=True)
                    if data.version != (row.version if row else 0):
                        raise ConflictException("须知配置已更新，请刷新并核对后保存")
                    content = JourneyContent.model_validate(data.model_dump(exclude={"version"})).model_dump()
                    for block in content["blocks"]:
                        if block["type"] != "image":
                            continue
                        # The editor only binds uploaded images; do not fetch arbitrary remote URLs.
                        from app.services.upload import UploadService
                        from PIL import Image
                        prefix = "/api/media/"
                        if not block["url"].startswith(prefix):
                            raise BusinessException("须知图片请通过上传入口选择JPG或PNG文件")
                        path = UploadService.get_file_path(block["url"][len(prefix):])
                        try:
                            if path.stat().st_size > 5 * 1024 * 1024:
                                raise BusinessException("须知图片不能超过5MB")
                            with Image.open(path) as image:
                                if image.format not in {"JPEG", "PNG"}:
                                    raise BusinessException("须知图片仅支持JPG、PNG实际文件")
                                image.verify()
                        except (OSError, ValueError, Image.DecompressionBombError) as error:
                            raise BusinessException("须知图片不存在或内容损坏，请重新上传") from error
                    if row is None:
                        row = CertificationJourneyContent(scope=content_scope(vendor, plan_id), vendor=vendor, plan_id=plan_id, content=content, version=1, updated_by=admin_id)
                        db.add(row)
                    else:
                        row.content, row.version, row.updated_by = content, row.version + 1, admin_id
                    await db.flush()
                    return {**content, "version": row.version}
        except IntegrityError as error:
            raise ConflictException("须知配置已更新，请刷新并核对后保存") from error

    async def notice(self, vendor, batch_id, registration_type=None):
        async with get_db_ctx() as db:
            return await self.notice_in_db(db, vendor, batch_id, registration_type)

    async def notice_in_db(self, db, vendor, batch_id, registration_type=None):
        catalog = await self.catalog_in_db(db, vendor)
        batch = next((item for item in catalog["batches"] if item["id"] == batch_id), None)
        if not batch or not batch["can_apply"]:
            raise BusinessException("该批次已截止、下架、名额已满或暂不可报名，请重新选择")
        price = next((item for item in batch["prices"] if item["value"] == registration_type), None)
        if price is None and registration_type is None and len(batch["prices"]) == 1:
            price = batch["prices"][0]
        if price is None:
            raise BusinessException("请先选择本批次的报名方式")
        plan = await db.get(Plan, batch["plan_id"])
        requirements_config = plan.renshe_config
        if vendor == "renshe":
            from app.services.renshe_reporting import resolve_contract
            requirements_config = (await resolve_contract(db, plan))["config"]
        general = await content_record(db, vendor)
        supplement = await content_record(db, vendor, plan.id)
        subject = next(item for item in catalog["subjects"] if item["id"] == batch["subject_id"])
        # Dynamic occupancy must not invalidate every user's confirmation; capacity/window/rules must.
        fixed_batch = {key: value for key, value in batch.items() if key not in {"remaining_count", "can_apply"}}
        material_limits = {}
        batch_model = {"h3c": H3cExamBatch, "nisp": NispExamBatch}.get(vendor)
        if batch_model:
            domain_batch = await db.scalar(select(batch_model).where(batch_model.plan_id == plan.id))
            material_limits = {"max_material_bytes": domain_batch.max_material_bytes}
        snapshot = {"vendor": vendor, "subject": subject, "batch": fixed_batch, "price": price,
            "notice": general.content if general else default_content(vendor),
            "supplement": supplement.content if supplement else default_content(vendor, batch=True),
            "batch_description": plan.description or "", "requirements": requirements_config if vendor == "renshe" else material_limits}
        # Internal Renshe export/storage rules are hashed, never exposed to anonymous readers.
        digest = notice_digest(snapshot)
        public = {key: value for key, value in snapshot.items() if key != "requirements"}
        for key in ("notice", "supplement"):
            public[key] = {field: snapshot[key][field] for field in ("title", "blocks")}
        requirements = []
        if vendor == "renshe":
            config = plan.renshe_config or {}
            profile = config.get("rule_profile") or {}
            requirements = [*profile.get("source_conditions", []), *profile.get("project_limits", [])]
            requirements.extend(f"{item.get('name', item['kind'])}：{'必交' if item.get('required') else '按申报情况提供'}" for item in config.get("materials", []))
        elif vendor == "h3c":
            requirements = [{"coupon": "考券报名：填写考券号，上传对应考券证明图片。", "student": "学生报名：有学信网验证码时，填写验证码并上传身份证人像面；无验证码时，上传学院盖章的学籍证图片。", "full": "全额报名：无需考券或学生资格证明。"}[price["value"]]]
        return {**jsonable_encoder(public), "requirements_summary": requirements, "notice_hash": digest, "_snapshot": jsonable_encoder(snapshot)}


async def confirmed_notice(db, *, vendor, batch_id, notice_hash, registration_type=None):
    current = await CertificationJourneyService().notice_in_db(db, vendor, batch_id, registration_type)
    if notice_hash != current["notice_hash"]:
        raise ConflictException("报名须知、费用或批次要求已变化，请重新阅读并确认；已填资料仍保留")
    return {**current["_snapshot"], "notice_hash": notice_hash, "confirmed_at": datetime.now(timezone.utc).isoformat()}


def progress_state(status, *, order_status, offline_status=None, payment_channel="bank_transfer", refunded=False, frozen=False):
    """No percentages; exceptions take priority over normal stage completion."""
    if refunded or order_status == "refunded" or status == "refunded_closed":
        return {"stage": None, "exception": True, "label": "已退款", "description": "本次报名已退款，正常审核流程已停止。", "action": "contact"}
    if frozen or status in {"refund_processing", "pending_refund_confirmation"}:
        return {"stage": None, "exception": True, "label": "退款处理中", "description": "请查看退款进展或联系管理员，正常审核流程已暂停。", "action": "payment"}
    if offline_status == "exception":
        return {"stage": None, "exception": True, "label": "资金异常", "description": "当前收款存在异常，请查看付款详情并联系管理员。", "action": "payment"}
    if status in {"cancelled", "closed"} or order_status == "closed":
        return {"stage": None, "exception": True, "label": "已关闭", "description": "本次报名已关闭，不再继续审核。", "action": "contact"}
    if order_status not in {"paid", "completed"}:
        if payment_channel == "free":
            return {"stage": 0, "exception": False, "label": "报名已提交，请确认零元报名", "description": "本次报名无需转账，请在订单中确认零元报名后进入审核。", "action": "payment"}
        if payment_channel != "bank_transfer":
            return {"stage": 0, "exception": False, "label": "报名已提交，待付款核验", "description": "原支付渠道订单请在付款详情核对，请勿重复转账。", "action": "payment"}
        if offline_status == "returned":
            return {"stage": 1, "exception": True, "label": "转账凭证待补交", "description": "请按退回原因补交转账凭证，并留意转账凭证提交截止。", "action": "payment"}
        if offline_status == "under_review":
            return {"stage": 1, "exception": False, "label": "等待核账", "description": "转账凭证已提交，请等待管理员确认到账。", "action": "payment"}
        return {"stage": 0, "exception": False, "label": "报名已提交，请完成线下转账申报", "description": "请查看收款账户，完成转账并提交转账信息与凭证。", "action": "payment"}
    if status in {"initial_rejected", "external_rejected", "returned", "rejected_awaiting_resubmission"}:
        return {"stage": 3 if status == "external_rejected" else 2, "exception": True, "label": "报名资料待补正", "description": "请按审核原因修改对应资料，补正后重新审核，无需重复付款。", "action": "correct"}
    if status in {"external_approved", "final_approved"}:
        return {"stage": 4, "exception": False, "label": "报名审核通过", "description": "本次报名审核已通过，请留意批次后续安排。", "action": "none"}
    if status in {"approved", "pending_external_review"}:
        return {"stage": 3, "exception": False, "label": "等待后续报送／终审", "description": "初审已通过，后续报送与审核由管理员办理。", "action": "none"}
    return {"stage": 2, "exception": False, "label": "等待初审", "description": "付款已确认，请等待管理员审核报名资料。", "action": "none"}


async def registration_context(db, vendor, registration_id, user_id):
    from app.domain.h3c.src.index import H3cRegistration
    from app.domain.nisp.src.index import NispRegistration
    from app.domain.renshe.src.index import RensheApplication
    if vendor == "sangfor":
        from app.services.sangfor import registration_status
        order = await db.scalar(select(Order).where(Order.id == registration_id, Order.user_id == user_id))
        if not order or (order.extra_data or {}).get("business_type") != "sangfor":
            raise NotFoundException("报名记录")
        return order, order, registration_status(order)
    model = {"h3c": H3cRegistration, "nisp": NispRegistration, "renshe": RensheApplication}[vendor]
    record = await db.scalar(select(model).where(model.id == registration_id, model.user_id == user_id))
    if record is None:
        raise NotFoundException("报名记录")
    order = await db.scalar(select(Order).where(Order.application_id == record.id, Order.user_id == user_id).order_by(Order.id.desc()).limit(1)) if vendor == "renshe" else await db.get(Order, record.order_id)
    if order is None or order.user_id != user_id:
        raise NotFoundException("已提交报名")
    return record, order, record.status


async def user_journey(vendor, registration_id, user_id):
    from app.models.offline_payment import OfflinePayment, OfflinePaymentAudit, OfflinePaymentSubmission
    from app.services.consultation_group import ConsultationGroupService
    from app.domain.h3c.src.index import H3cReview, H3cFinalReview
    from app.domain.nisp.src.index import NispReview, NispFinalReview
    from app.domain.renshe.src.index import RensheReview
    async with get_db_ctx() as db:
        record, order, status = await registration_context(db, vendor, registration_id, user_id)
        plan = await db.get(Plan, order.plan_id)
        if plan is None:
            raise NotFoundException("认证批次")
        product = await db.scalar(select(CertProduct).where(CertProduct.code == plan.product_type))
        offline = await db.scalar(select(OfflinePayment).where(OfflinePayment.order_id == order.id))
        submission = await db.scalar(select(OfflinePaymentSubmission).where(OfflinePaymentSubmission.order_id == order.id).order_by(OfflinePaymentSubmission.version.desc()).limit(1))
        refund_approved = await db.scalar(select(OfflinePaymentAudit.id).where(OfflinePaymentAudit.order_id == order.id, OfflinePaymentAudit.action == "admin.generic_refund_approved").limit(1))
        state = progress_state(status, order_status=order.status, offline_status=order.offline_status, payment_channel=order.payment_channel, frozen=bool(getattr(record, "frozen_at", None) or (refund_approved and order.status in {"paid", "completed"})))
        general, supplement = await content_record(db, vendor), await content_record(db, vendor, plan.id)
        contacts = {}
        account = offline.account_snapshot if offline else {}
        for field in ("contact_name", "contact_phone", "contact_wechat", "service_hours"):
            contacts[field] = ((supplement.content if supplement else {}).get(field) or (general.content if general else {}).get(field) or getattr(plan, field, None) or account.get(field) or "")
        group = None
        if order.status in {"paid", "completed"} and order.offline_status != "exception":
            group_status = {"final_approved": "approved", "returned": "rejected_awaiting_resubmission"}.get(status, status)
            group = await ConsultationGroupService.get_user_group(db, plan=plan, registration_status=group_status)
            if group is None and vendor == "nisp":
                batch = await db.get(NispExamBatch, record.batch_id)
                group = ConsultationGroupService.legacy_nisp_group(batch=batch, plan=plan, registration_status=status) if batch else None
        events = [{"at": order.created_at, "label": "已创建报名"}]
        if order.offline_submitted_at:
            events.append({"at": order.offline_submitted_at, "label": "已提交转账凭证"})
        if order.paid_at:
            events.append({"at": order.paid_at, "label": "付款已确认" if order.price else "零元报名，无需付款"})
        reason = submission.reason if order.offline_status == "returned" and submission else None
        if order.offline_status == "exception" and offline:
            reason = offline.message
        due = offline.deadline if offline and order.status == "pending" else None
        correction_due = getattr(record, "correction_due_at", None) or getattr(record, "resubmission_due_at", None)
        if vendor == "sangfor":
            extra = order.extra_data or {}
            correction_due = extra.get("correction_due_at")
            for review in extra.get("reviews", []):
                label = {"approve": "初审通过", "return": "初审退回补正", "final_approve": "终审通过", "final_return": "终审退回补正"}.get(review["action"])
                if label:
                    events.append({"at": review["created_at"], "label": label, "reason": review.get("reason")})
            if status == "returned" and extra.get("reviews"):
                reason = extra["reviews"][-1].get("reason")
        else:
            model = {"h3c": H3cReview, "nisp": NispReview, "renshe": RensheReview}[vendor]
            owner = model.application_id if vendor == "renshe" else model.registration_id
            reviews = list((await db.scalars(select(model).where(owner == registration_id).order_by(model.id))).all())
            for review in reviews:
                stage = "外审" if getattr(review, "stage", None) == "external" else "初审"
                review_reason = getattr(review, "reason", None) or getattr(review, "reason_detail", None)
                events.append({"at": review.reviewed_at, "label": stage + ("通过" if review.decision == "approved" else "退回"), "reason": review_reason})
                if review.decision != "approved" and state["action"] == "correct":
                    reason = review_reason
            if vendor in {"h3c", "nisp"}:
                final_model = H3cFinalReview if vendor == "h3c" else NispFinalReview
                finals = list((await db.scalars(select(final_model).where(final_model.registration_id == registration_id).order_by(final_model.id))).all())
                for review in finals:
                    events.append({"at": review.reviewed_at, "label": "终审通过" if review.decision == "approved" else "终审退回", "reason": getattr(review, "reason_detail", None)})
        if status == "external_rejected":
            state["description"] = "外审退回，请按原因补正资料；补正后需重新初审及报送。"
        if vendor == "renshe" and state["stage"] == 3 and not state["exception"]:
            state["label"] = "等待后续报送／外审"
        events = sorted(jsonable_encoder(events), key=lambda item: item["at"])
        return jsonable_encoder({"vendor": vendor, "registration_id": registration_id, "registration_no": getattr(record, "registration_no", None) or order.out_trade_no,
            "order_id": order.id, "subject": product.chinese_name if product else plan.project_name or plan.product_type, "batch_name": plan.name,
            "stages": ["已提交", "核账", "初审", "外审" if vendor == "renshe" else "终审", "办理完成"], "progress": state,
            "reason": reason, "apply_end": plan.apply_end, "proof_due_at": due, "correction_due_at": correction_due,
            "events": events, "contact": contacts, "consultation_group": group, "group_message": "咨询群按核账及批次有效期开放；暂不可查看时可联系管理员。"})
