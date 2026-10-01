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
        }
