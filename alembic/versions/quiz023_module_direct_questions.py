"""Allow quiz questions to attach directly to modules without knowledge points (quiz023).

甲方口径（2026-11-11）：模块下可以不要子项目，题目直挂模块；
单链（库→唯一模块→唯一知识点）目录塌缩，删知识点支持题目下沉。
新增 ``quiz_question.module_id``（V2 题恒非空，迁移时自知识点反填），
``knowledge_point_id`` 变为可选；两列一致性由服务层维护，
DB 约束只保证「库↔模块」配对与「可练习题目必属一个模块」。
"""

from alembic import op
import sqlalchemy as sa

revision = "quiz023"
down_revision = "quiz022"
branch_labels = None
depends_on = None

# 原约束：库与知识点必须同时非空（题目只能挂知识点）。
_OLD_CONSTRAINT = (
    "(library_id IS NULL AND knowledge_point_id IS NULL) OR "
    "(library_id IS NOT NULL AND knowledge_point_id IS NOT NULL)"
)
# 放宽后：V2 题必须挂模块（module_id 非空），知识点可选。
_NEW_CONSTRAINT = (
    "(library_id IS NULL AND knowledge_point_id IS NULL AND module_id IS NULL) OR "
    "(library_id IS NOT NULL AND module_id IS NOT NULL)"
)


def upgrade():
    op.add_column("quiz_question", sa.Column("module_id", sa.BigInteger(), nullable=True))
    # 反填：有知识点的题目取知识点所属模块；V2 题必有知识点，反填后 module_id 恒非空。
    op.execute(
        "UPDATE quiz_question SET module_id = "
        "(SELECT module_id FROM quiz_knowledge_point WHERE quiz_knowledge_point.id = quiz_question.knowledge_point_id) "
        "WHERE knowledge_point_id IS NOT NULL"
    )
    op.drop_constraint("ck_quiz_question_v2_assignment", "quiz_question", type_="check")
    op.create_check_constraint(
        "ck_quiz_question_v2_assignment", "quiz_question", _NEW_CONSTRAINT
    )
    op.create_foreign_key(
        "fk_quiz_question_module_library",
        "quiz_question",
        "quiz_module",
        ["module_id", "library_id"],
        ["id", "library_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_quiz_question_module_pool",
        "quiz_question",
        ["library_id", "module_id", "status", "id"],
    )


def downgrade():
    # 直挂模块（无知识点）的题目在旧结构无法表示，须先迁回知识点或删除。
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM quiz_question "
        "WHERE module_id IS NOT NULL AND knowledge_point_id IS NULL LIMIT 1"
    )).scalar():
        raise RuntimeError(
            "存在直挂模块的知识点缺失题目，请先移回知识点或删除后再回退"
        )
    op.drop_index("ix_quiz_question_module_pool", table_name="quiz_question")
    op.drop_constraint("fk_quiz_question_module_library", "quiz_question", type_="foreignkey")
    op.drop_constraint("ck_quiz_question_v2_assignment", "quiz_question", type_="check")
    op.create_check_constraint(
        "ck_quiz_question_v2_assignment", "quiz_question", _OLD_CONSTRAINT
    )
    op.drop_column("quiz_question", "module_id")
