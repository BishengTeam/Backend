"""Question eligibility shared by the V2 catalog, practice, and admin counts."""

from sqlalchemy import and_, case, func, select

from app.domain.community.src.index import (
    QuizKnowledgePoint,
    QuizModule,
    QuizQuestion,
    QuizQuestionRevision,
)


def usable_ownership():
    return and_(
        QuizKnowledgePoint.library_id == QuizQuestion.library_id,
        QuizModule.library_id == QuizQuestion.library_id,
        QuizKnowledgePoint.status == "active",
        QuizModule.status == "active",
        QuizKnowledgePoint.system_kind == "none",
        QuizModule.system_kind == "none",
    )


def with_question_relations(stmt, *, outer=False):
    return (
        stmt.select_from(QuizQuestion)
        .join_from(
            QuizQuestion,
            QuizKnowledgePoint,
            QuizKnowledgePoint.id == QuizQuestion.knowledge_point_id,
            isouter=outer,
        )
        .join_from(
            QuizKnowledgePoint,
            QuizModule,
            QuizModule.id == QuizKnowledgePoint.module_id,
            isouter=outer,
        )
        .join_from(
            QuizQuestion,
            QuizQuestionRevision,
            and_(
                QuizQuestionRevision.id == QuizQuestion.current_revision_id,
                QuizQuestionRevision.question_id == QuizQuestion.id,
                QuizQuestionRevision.status == "published",
            ),
            isouter=outer,
        )
    )


def practice_pool(stmt):
    return with_question_relations(stmt).where(
        QuizQuestion.status == "published", usable_ownership()
    )


async def practice_count_breakdown(db, library_ids):
    """Assign one reason per non-deleted question in the requested libraries."""
    reasons = ("practicable", "unpublished", "disabled", "unavailable_ownership", "invalid_revision")
    result = {library_id: dict.fromkeys(reasons, 0) for library_id in library_ids}
    if not library_ids:
        return result
    reason = case(
        (QuizQuestion.status == "draft", "unpublished"),
        (QuizQuestion.status == "disabled", "disabled"),
        (usable_ownership().is_not(True), "unavailable_ownership"),
        (QuizQuestionRevision.id.is_(None), "invalid_revision"),
        else_="practicable",
    ).label("reason")
    rows = with_question_relations(
        select(QuizQuestion.library_id, reason, func.count()), outer=True
    ).where(
        QuizQuestion.library_id.in_(library_ids), QuizQuestion.status != "deleted"
    ).group_by(QuizQuestion.library_id, reason)
    for library_id, name, count in (await db.execute(rows)).all():
        result[library_id][name] = count
    return result
