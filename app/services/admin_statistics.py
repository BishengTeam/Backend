from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select

from app.adapter.database import get_db_ctx
from app.domain.certification.src.model.competition import Competition, CompetitionReg
from app.domain.certification.src.model.course import Course
from app.domain.classroom.src.model.classroom import Classroom
from app.domain.community.src.index import Conversation, QuizLibrary, QuizPracticeAttempt, QuizQuestion
from app.domain.content.src.index import Ticket
from app.domain.h3c.src.index import H3cRegistration
from app.domain.nisp.src import NispRegistration
from app.domain.order.src.index import Order
from app.domain.user.src.index import User
from app.port.config import settings
from app.services.admin_order import cash_event_ledger
from app.services.quiz_question_pool import practice_pool


class AdminStatisticsService:
    async def dashboard(self) -> dict:
        zone = ZoneInfo(settings.APP_TIMEZONE)
        today = datetime.now(zone).date()
        tomorrow = today + timedelta(days=1)
        first_day = tomorrow - timedelta(days=30)
        start = datetime.combine(first_day, time.min, tzinfo=zone)
        end = datetime.combine(tomorrow, time.min, tzinfo=zone)
        dates = [first_day + timedelta(days=offset) for offset in range(30)]

        def in_window(column):
            return (column >= start) & (column < end)

        def local_day(column):
            return func.date(func.timezone(settings.APP_TIMEZONE, column))

        def trend(rows):
            values = dict(rows)
            return [{"date": day.isoformat(), "value": int(values.get(day, 0))} for day in dates]

        paid = Order.status.in_(("paid", "completed"))
        async with get_db_ctx() as db:
            async def count(model, *conditions):
                return await db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0

            total_users = await count(User)
            total_orders, recent_orders, paid_orders, closed_orders, refunded_orders = (
                await db.execute(
                    select(
                        func.count(Order.id),
                        func.count(Order.id).filter(in_window(Order.created_at)),
                        func.count(Order.id).filter(paid),
                        func.count(Order.id).filter(Order.status == "closed"),
                        func.count(Order.id).filter(Order.status == "refunded"),
                    )
                )
            ).one()

            ledger = cash_event_ledger(None, end)
            amount = case((ledger.c.kind == "receipt", ledger.c.amount), else_=-ledger.c.amount)
            revenue = await db.scalar(select(func.coalesce(func.sum(amount), 0))) or 0

            ledger = cash_event_ledger(start, end)
            amount = case((ledger.c.kind == "receipt", ledger.c.amount), else_=-ledger.c.amount)
            day = local_day(ledger.c.occurred_at)
            revenue_trend = trend(
                (await db.execute(select(day, func.sum(amount)).group_by(day))).all()
            )

            day = local_day(User.created_at)
            user_trend = trend(
                (await db.execute(
                    select(day, func.count(User.id)).where(in_window(User.created_at)).group_by(day)
                )).all()
            )
            day = local_day(QuizPracticeAttempt.submitted_at)
            quiz_trend = trend(
                (await db.execute(
                    select(day, func.count(QuizPracticeAttempt.id))
                    .where(in_window(QuizPracticeAttempt.submitted_at)).group_by(day)
                )).all()
            )

            # Match the V2 library list and the quiz statistics page.
            quiz_questions_inventory_total, quiz_questions_total = (
                await db.execute(
                    select(
                        func.count(QuizQuestion.id),
                        func.count(QuizQuestion.id).filter(QuizQuestion.status == "published"),
                    )
                    .join(QuizLibrary, QuizLibrary.id == QuizQuestion.library_id)
                    .where(QuizLibrary.status != "deleted", QuizQuestion.status != "deleted")
                )
            ).one()
            quiz_questions_practicable_total = await db.scalar(
                practice_pool(select(func.count()))
                .join_from(QuizQuestion, QuizLibrary, QuizLibrary.id == QuizQuestion.library_id)
                .where(QuizLibrary.status != "deleted")
            )
            quiz_libraries_total = await count(QuizLibrary, QuizLibrary.status == "published")

            conversations_total = await count(Conversation)
            conversations_30d = await count(Conversation, in_window(Conversation.created_at))
            tickets_total = await count(Ticket)
            tickets_waiting_manual = await count(Ticket, Ticket.status == "waiting_manual")

            cert_status = {}
            for name, registration in (("h3c", H3cRegistration), ("nisp", NispRegistration)):
                counts = (await db.execute(
                    select(registration.status, func.count()).group_by(registration.status)
                )).all()
                by_status = dict(counts)
                cert_status[name] = {
                    status: by_status.get(status, 0)
                    for status in ("pending_review", "approved", "final_approved")
                }

            courses_total = await count(Course, Course.status == "published")
            competitions_active = await count(Competition, Competition.is_active.is_(True))
            competition_registrations = await count(CompetitionReg)
            classrooms_active = await count(Classroom, Classroom.status == "active")

            order_type_rows = (await db.execute(
                select(Order.product_type, func.count()).where(paid).group_by(Order.product_type)
            )).all()

        terminal_payment_orders = paid_orders + closed_orders + refunded_orders
        return {
            "total_users": total_users,
            "total_orders": total_orders,
            "recent_orders_30d": recent_orders,
            "paid_orders": paid_orders,
            "refunded_orders": refunded_orders,
            "revenue_fen": revenue,
            "recent_revenue_30d_fen": sum(point["value"] for point in revenue_trend),
            "conversion_rate": round(paid_orders / total_orders * 100, 2) if total_orders else 0,
            # Retained for existing API consumers.
            "payment_success_rate": (
                round(paid_orders / terminal_payment_orders * 100, 2)
                if terminal_payment_orders else 0
            ),
            "ai_conversations_total": conversations_total,
            "ai_conversations_30d": conversations_30d,
            "tickets_total": tickets_total,
            "tickets_waiting_manual": tickets_waiting_manual,
            "manual_transfer_rate": round(tickets_waiting_manual / tickets_total * 100, 2) if tickets_total else 0,
            "quiz_questions_total": quiz_questions_total,
            "quiz_questions_inventory_total": quiz_questions_inventory_total,
            "quiz_questions_practicable_total": quiz_questions_practicable_total,
            "quiz_libraries_total": quiz_libraries_total,
            "quiz_attempts_today": quiz_trend[-1]["value"],
            "quiz_attempts_30d": sum(point["value"] for point in quiz_trend),
            "h3c_pending_review": cert_status["h3c"]["pending_review"],
            "h3c_approved": cert_status["h3c"]["approved"],
            "nisp_pending_review": cert_status["nisp"]["pending_review"],
            "nisp_approved": cert_status["nisp"]["approved"],
            "courses_total": courses_total,
            "competitions_active": competitions_active,
            "competition_registrations": competition_registrations,
            "classrooms_active": classrooms_active,
            "revenue_trend": revenue_trend,
            "user_trend": user_trend,
            "quiz_trend": quiz_trend,
            "order_type_distribution": [
                {"name": name, "value": count} for name, count in order_type_rows
            ],
            "cert_status": cert_status,
        }
