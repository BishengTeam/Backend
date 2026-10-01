from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order
from app.domain.user.src.index import User


class AdminStatisticsService:

    async def dashboard(self) -> dict:
        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)

        async with get_db_ctx() as db:
            total_users = (
                await db.execute(select(func.count()).select_from(User))
            ).scalar() or 0

            order_base = select(Order)
            total_orders = (
                await db.execute(
                    select(func.count()).select_from(order_base.subquery())
                )
            ).scalar() or 0

            recent_orders = (
                await db.execute(
                    select(func.count()).select_from(Order).where(
                        Order.created_at >= thirty_days_ago
                    )
                )
            ).scalar() or 0

            paid_orders = (
                await db.execute(
                    select(func.count()).select_from(Order).where(
                        Order.status.in_(["paid", "completed"])
                    )
                )
            ).scalar() or 0

            revenue = (
                await db.execute(
                    select(func.coalesce(func.sum(Order.price), 0)).select_from(Order).where(
                        Order.status.in_(["paid", "completed"])
                    )
                )
            ).scalar() or 0

            recent_revenue = (
                await db.execute(
                    select(func.coalesce(func.sum(Order.price), 0))
                    .select_from(Order)
                    .where(
                        Order.status.in_(["paid", "completed"]),
                        Order.created_at >= thirty_days_ago,
                    )
                )
            ).scalar() or 0

            conversion_rate = round(paid_orders / total_orders * 100, 2) if total_orders > 0 else 0

        # ── Quiz module stats ──
        from app.domain.community.src.index import QuizQuestion, QuizLibrary, QuizPracticeAttempt

        quiz_questions_total = (
            await db.execute(
                select(func.count()).select_from(QuizQuestion).where(
                    QuizQuestion.status == "published"
                )
            )
        ).scalar() or 0

        quiz_libraries_total = (
            await db.execute(
                select(func.count()).select_from(QuizLibrary).where(
                    QuizLibrary.status == "published"
                )
            )
        ).scalar() or 0

        quiz_attempts_today = (
            await db.execute(
                select(func.count()).select_from(QuizPracticeAttempt).where(
                    QuizPracticeAttempt.submitted_at >= now.replace(hour=0, minute=0, second=0)
                )
            )
        ).scalar() or 0

        quiz_attempts_30d = (
            await db.execute(
                select(func.count()).select_from(QuizPracticeAttempt).where(
                    QuizPracticeAttempt.submitted_at >= thirty_days_ago
                )
            )
        ).scalar() or 0

        # ── Certification stats ──
        from app.domain.h3c.src.index import H3cRegistration
        from app.domain.nisp.src import NispRegistration

        h3c_pending_review = (
            await db.execute(
                select(func.count()).select_from(H3cRegistration).where(
                    H3cRegistration.status == "pending_review"
                )
            )
        ).scalar() or 0

        h3c_approved = (
            await db.execute(
                select(func.count()).select_from(H3cRegistration).where(
                    H3cRegistration.status == "approved"
                )
            )
        ).scalar() or 0

        nisp_pending_review = (
            await db.execute(
                select(func.count()).select_from(NispRegistration).where(
                    NispRegistration.status == "pending_review"
                )
            )
        ).scalar() or 0

        nisp_approved = (
            await db.execute(
                select(func.count()).select_from(NispRegistration).where(
                    NispRegistration.status == "approved"
                )
            )
        ).scalar() or 0

        # ── Course stats ──
        from app.domain.certification.src.model.course import Course

        courses_total = (
            await db.execute(
                select(func.count()).select_from(Course).where(
                    Course.status == "published"
                )
            )
        ).scalar() or 0

        # ── Competition stats ──
        from app.domain.certification.src.model.competition import Competition, CompetitionReg

        competitions_active = (
            await db.execute(
                select(func.count()).select_from(Competition).where(
                    Competition.is_active == True
                )
            )
        ).scalar() or 0

        competition_registrations = (
            await db.execute(
                select(func.count()).select_from(CompetitionReg)
            )
        ).scalar() or 0

        # ── Classroom stats ──
        from app.domain.classroom.src.model.classroom import Classroom

        classrooms_active = (
            await db.execute(
                select(func.count()).select_from(Classroom).where(
                    Classroom.status == "active"
                )
            )
        ).scalar() or 0

        # ── 30-day trends ──
        # Daily revenue
        revenue_rows = (
            await db.execute(
                select(
                    func.date_trunc("day", Order.created_at).label("day"),
                    func.coalesce(func.sum(Order.price), 0).label("revenue"),
                )
                .where(
                    Order.status.in_(["paid", "completed"]),
                    Order.created_at >= thirty_days_ago,
                )
                .group_by("day")
                .order_by("day")
            )
        ).all()
        revenue_by_day = {str(r.day.date()): int(r.revenue) for r in revenue_rows}

        # Daily new users
        user_rows = (
            await db.execute(
                select(
                    func.date_trunc("day", User.created_at).label("day"),
                    func.count().label("count"),
                )
                .where(User.created_at >= thirty_days_ago)
                .group_by("day")
                .order_by("day")
            )
        ).all()
        users_by_day = {str(r.day.date()): int(r.count) for r in user_rows}

        # Daily quiz attempts
        quiz_rows = (
            await db.execute(
                select(
                    func.date_trunc("day", QuizPracticeAttempt.submitted_at).label("day"),
                    func.count().label("count"),
                )
                .where(QuizPracticeAttempt.submitted_at >= thirty_days_ago)
                .group_by("day")
                .order_by("day")
            )
        ).all()
        quiz_by_day = {str(r.day.date()): int(r.count) for r in quiz_rows}

        # Build continuous 30-day series
        from datetime import date as date_type
        trend_dates = []
        for i in range(30):
            d = (now - timedelta(days=29 - i)).date()
            trend_dates.append(d.isoformat())

        revenue_trend = [
            {"date": d, "value": revenue_by_day.get(d, 0)} for d in trend_dates
        ]
        user_trend = [
            {"date": d, "value": users_by_day.get(d, 0)} for d in trend_dates
        ]
        quiz_trend = [
            {"date": d, "value": quiz_by_day.get(d, 0)} for d in trend_dates
        ]

        # ── Order type distribution (pie chart) ──
        order_type_rows = (
            await db.execute(
                select(
                    Order.product_type,
                    func.count().label("count"),
                )
                .where(Order.status.in_(["paid", "completed"]))
                .group_by(Order.product_type)
            )
        ).all()
        order_type_distribution = [
            {"name": r.product_type, "value": int(r.count)} for r in order_type_rows
        ]

        # ── Certification status breakdown ──
        cert_status = {
            "h3c": {
                "pending_review": h3c_pending_review,
                "approved": h3c_approved,
            },
            "nisp": {
                "pending_review": nisp_pending_review,
                "approved": nisp_approved,
            },
        }

        return {
            "total_users": total_users,
            "total_orders": total_orders,
            "recent_orders_30d": recent_orders,
            "paid_orders": paid_orders,
            "revenue_fen": revenue,
            "recent_revenue_30d_fen": recent_revenue,
            "conversion_rate": conversion_rate,
            # Quiz
            "quiz_questions_total": quiz_questions_total,
            "quiz_libraries_total": quiz_libraries_total,
            "quiz_attempts_today": quiz_attempts_today,
            "quiz_attempts_30d": quiz_attempts_30d,
            # Certification
            "h3c_pending_review": h3c_pending_review,
            "h3c_approved": h3c_approved,
            "nisp_pending_review": nisp_pending_review,
            "nisp_approved": nisp_approved,
            # Course
            "courses_total": courses_total,
            # Competition
            "competitions_active": competitions_active,
            "competition_registrations": competition_registrations,
            # Classroom
            "classrooms_active": classrooms_active,
            # Trends
            "revenue_trend": revenue_trend,
            "user_trend": user_trend,
            "quiz_trend": quiz_trend,
            "order_type_distribution": order_type_distribution,
            "cert_status": cert_status,
        }
