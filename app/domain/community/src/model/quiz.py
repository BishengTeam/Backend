"""SQLAlchemy models for the frozen quiz-domain schema."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base


class _QuizTimestampMixin:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class _QuizCreatedAtMixin:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QuizCategory(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_category"
    __table_args__ = (
        CheckConstraint("depth BETWEEN 1 AND 3", name="ck_quiz_category_depth"),
        CheckConstraint(
            "status IN ('active', 'disabled')", name="ck_quiz_category_status"
        ),
        CheckConstraint("lock_version >= 1", name="ck_quiz_category_lock_version"),
        Index(
            "uq_quiz_category_root_name",
            "normalized_name",
            unique=True,
            postgresql_where=text("parent_id IS NULL"),
            sqlite_where=text("parent_id IS NULL"),
        ),
        Index(
            "uq_quiz_category_sibling_name",
            "parent_id",
            "normalized_name",
            unique=True,
            postgresql_where=text("parent_id IS NOT NULL"),
            sqlite_where=text("parent_id IS NOT NULL"),
        ),
        Index("ix_quiz_category_parent_sort", "parent_id", "sort_order", "id"),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("quiz_category.id", ondelete="RESTRICT")
    )
    depth: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    description: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    ever_had_question: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False
    )


class QuizLibrary(Base, _QuizTimestampMixin):
    """The fixed V2 quiz ownership and entitlement boundary."""

    __tablename__ = "quiz_library"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'suspended', 'archived', 'deleted')",
            name="ck_quiz_library_status",
        ),
        CheckConstraint(
            "access_mode IN ('access_mode_pending', 'free', 'course_entitlement')",
            name="ck_quiz_library_access_mode",
        ),
        CheckConstraint(
            "system_kind IN ('none', 'migration_quarantine')",
            name="ck_quiz_library_system_kind",
        ),
        CheckConstraint(
            "migration_state IN ('pending_review', 'needs_organization', 'ready')",
            name="ck_quiz_library_migration_state",
        ),
        CheckConstraint("lock_version >= 1", name="ck_quiz_library_lock_version"),
        CheckConstraint(
            "((status = 'draft' AND published_at IS NULL AND suspended_at IS NULL "
            "AND archived_at IS NULL AND deleted_at IS NULL) OR "
            "(status = 'published' AND published_at IS NOT NULL "
            "AND suspended_at IS NULL AND archived_at IS NULL AND deleted_at IS NULL) OR "
            "(status = 'suspended' AND published_at IS NOT NULL "
            "AND suspended_at IS NOT NULL AND archived_at IS NULL AND deleted_at IS NULL) OR "
            "(status = 'archived' AND archived_at IS NOT NULL AND deleted_at IS NULL) OR "
            "(status = 'deleted' AND archived_at IS NOT NULL AND deleted_at IS NOT NULL))",
            name="ck_quiz_library_lifecycle",
        ),
        Index(
            "uq_quiz_library_reserved_name",
            "normalized_name",
            unique=True,
            postgresql_where=text("name_reserved = true"),
            sqlite_where=text("name_reserved = true"),
        ),
        Index("ix_quiz_library_catalog", "v2_enabled", "status", "sort_order", "id"),
        Index("ix_quiz_library_cleanup", "status", "restore_until", "id"),
    )

    library_code: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, server_default=text("quiz_library_code()")
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512))
    cover_url: Mapped[str | None] = mapped_column(String(512))
    details: Mapped[str | None] = mapped_column(Text)
    access_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="access_mode_pending", server_default="access_mode_pending"
    )
    system_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none", server_default="none"
    )
    migration_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending_review", server_default="pending_review"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )
    v2_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    name_reserved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restore_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )
    updated_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )


class QuizModule(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_module"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'disabled', 'deleted')",
            name="ck_quiz_module_status",
        ),
        CheckConstraint(
            "system_kind IN ('none', 'pending_organization')",
            name="ck_quiz_module_system_kind",
        ),
        CheckConstraint("lock_version >= 1", name="ck_quiz_module_lock_version"),
        CheckConstraint(
            "((status = 'active' AND disabled_at IS NULL AND deleted_at IS NULL) OR "
            "(status = 'disabled' AND disabled_at IS NOT NULL AND deleted_at IS NULL) OR "
            "(status = 'deleted' AND disabled_at IS NOT NULL AND deleted_at IS NOT NULL))",
            name="ck_quiz_module_lifecycle",
        ),
        UniqueConstraint("id", "library_id", name="uq_quiz_module_id_library"),
        Index(
            "uq_quiz_module_reserved_name",
            "library_id",
            "normalized_name",
            unique=True,
            postgresql_where=text("name_reserved = true"),
            sqlite_where=text("name_reserved = true"),
        ),
        Index("ix_quiz_module_tree", "library_id", "sort_order", "id"),
        Index("ix_quiz_module_cleanup", "status", "restore_until", "id"),
    )

    library_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_library.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    system_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none", server_default="none"
    )
    name_reserved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restore_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )
    updated_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )


class QuizKnowledgePoint(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_knowledge_point"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'disabled', 'deleted')",
            name="ck_quiz_knowledge_point_status",
        ),
        CheckConstraint(
            "system_kind IN ('none', 'uncategorized')",
            name="ck_quiz_knowledge_point_system_kind",
        ),
        CheckConstraint(
            "lock_version >= 1", name="ck_quiz_knowledge_point_lock_version"
        ),
        CheckConstraint(
            "((status = 'active' AND disabled_at IS NULL AND deleted_at IS NULL) OR "
            "(status = 'disabled' AND disabled_at IS NOT NULL AND deleted_at IS NULL) OR "
            "(status = 'deleted' AND disabled_at IS NOT NULL AND deleted_at IS NOT NULL))",
            name="ck_quiz_knowledge_point_lifecycle",
        ),
        ForeignKeyConstraint(
            ["module_id", "library_id"],
            ["quiz_module.id", "quiz_module.library_id"],
            name="fk_quiz_knowledge_point_module_library",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "library_id", name="uq_quiz_knowledge_point_id_library"),
        Index(
            "uq_quiz_knowledge_point_reserved_name",
            "module_id",
            "normalized_name",
            unique=True,
            postgresql_where=text("name_reserved = true"),
            sqlite_where=text("name_reserved = true"),
        ),
        Index("ix_quiz_knowledge_point_tree", "library_id", "module_id", "sort_order", "id"),
        Index("ix_quiz_knowledge_point_cleanup", "status", "restore_until", "id"),
    )

    library_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    module_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    system_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none", server_default="none"
    )
    name_reserved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restore_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )
    updated_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )


class QuizQuestion(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_question"
    __table_args__ = (
        CheckConstraint(
            "question_type IN ('single_choice', 'multiple_choice', 'judge')",
            name="ck_quiz_question_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'disabled', 'deleted')",
            name="ck_quiz_question_status",
        ),
        CheckConstraint(
            "((status = 'draft' AND ever_published = false "
            "AND published_at IS NULL AND disabled_at IS NULL AND deleted_at IS NULL) OR "
            "(status = 'published' AND ever_published = true "
            "AND published_at IS NOT NULL AND deleted_at IS NULL) OR "
            "(status = 'disabled' AND ever_published = true "
            "AND published_at IS NOT NULL AND disabled_at IS NOT NULL AND deleted_at IS NULL) OR "
            "(status = 'deleted' AND deleted_at IS NOT NULL))",
            name="ck_quiz_question_lifecycle",
        ),
        CheckConstraint("lock_version >= 1", name="ck_quiz_question_lock_version"),
        CheckConstraint(
            "(library_id IS NULL AND knowledge_point_id IS NULL) OR "
            "(library_id IS NOT NULL AND knowledge_point_id IS NOT NULL)",
            name="ck_quiz_question_v2_assignment",
        ),
        UniqueConstraint(
            "category_id",
            "question_text_hash",
            name="uq_quiz_question_category_text_hash",
        ),
        ForeignKeyConstraint(
            ["knowledge_point_id", "library_id"],
            ["quiz_knowledge_point.id", "quiz_knowledge_point.library_id"],
            name="fk_quiz_question_knowledge_point_library",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["current_revision_id", "id"],
            ["quiz_question_revision.id", "quiz_question_revision.question_id"],
            name="fk_quiz_question_current_revision",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["pending_revision_id", "id"],
            ["quiz_question_revision.id", "quiz_question_revision.question_id"],
            name="fk_quiz_question_pending_revision",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        Index(
            "ix_quiz_question_pool",
            "category_id",
            "status",
            "question_type",
            "id",
        ),
        Index("ix_quiz_question_updated", "updated_at", "id"),
        Index(
            "uq_quiz_question_library_text_hash",
            "library_id",
            "question_text_hash",
            unique=True,
            postgresql_where=text("library_id IS NOT NULL AND stem_reserved = true"),
            sqlite_where=text("library_id IS NOT NULL AND stem_reserved = true"),
        ),
        Index(
            "ix_quiz_question_v2_pool",
            "library_id",
            "knowledge_point_id",
            "status",
            "id",
        ),
    )

    library_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("quiz_library.id", ondelete="RESTRICT")
    )
    knowledge_point_id: Mapped[int | None] = mapped_column(
        BigInteger
    )
    category_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("quiz_category.id", ondelete="RESTRICT"),
        nullable=True,
    )
    question_type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )
    question_text: Mapped[str] = mapped_column(String(1024), nullable=False)
    normalized_question_text: Mapped[str] = mapped_column(String(1024), nullable=False)
    question_text_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    options: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    correct_answer: Mapped[str | list[str] | None] = mapped_column(JSONB)
    explanation: Mapped[str | None] = mapped_column(String(1024))
    ever_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restore_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stem_reserved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    current_revision_id: Mapped[int | None] = mapped_column(BigInteger)
    pending_revision_id: Mapped[int | None] = mapped_column(BigInteger)
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False
    )


class QuizQuestionRevision(Base, _QuizCreatedAtMixin):
    """Immutable content of a logical question."""

    __tablename__ = "quiz_question_revision"
    __table_args__ = (
        CheckConstraint(
            "question_type IN ('single_choice', 'multiple_choice', 'judge')",
            name="ck_quiz_question_revision_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'superseded', 'discarded')",
            name="ck_quiz_question_revision_status",
        ),
        CheckConstraint("revision_no >= 1", name="ck_quiz_question_revision_no"),
        CheckConstraint(
            "((status = 'published' AND published_at IS NOT NULL) OR "
            "(status <> 'published'))",
            name="ck_quiz_question_revision_lifecycle",
        ),
        UniqueConstraint(
            "question_id", "revision_no", name="uq_quiz_question_revision_number"
        ),
        UniqueConstraint(
            "id", "question_id", name="uq_quiz_question_revision_id_question"
        ),
        Index(
            "uq_quiz_question_pending_revision",
            "question_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
            sqlite_where=text("status = 'draft'"),
        ),
        Index("ix_quiz_question_revision_question", "question_id", "revision_no"),
    )

    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_question.id", ondelete="RESTRICT"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    question_type: Mapped[str] = mapped_column(String(24), nullable=False)
    question_text: Mapped[str] = mapped_column(String(1024), nullable=False)
    normalized_question_text: Mapped[str] = mapped_column(String(1024), nullable=False)
    question_text_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    options: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    correct_answer: Mapped[str | list[str] | None] = mapped_column(JSONB)
    explanation: Mapped[str | None] = mapped_column(String(1024))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )


class QuizCourseLibraryBinding(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_course_library_binding"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_quiz_course_library_binding_status",
        ),
        CheckConstraint(
            "lock_version >= 1", name="ck_quiz_course_library_binding_lock_version"
        ),
        UniqueConstraint(
            "course_id", "library_id", name="uq_quiz_course_library_binding"
        ),
        Index(
            "ix_quiz_course_library_binding_active",
            "course_id",
            "status",
            "library_id",
        ),
    )

    course_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("course.id", ondelete="RESTRICT"), nullable=False
    )
    library_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_library.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )
    updated_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )


class QuizLibraryEntitlement(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_library_entitlement"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'revoked', 'expired')",
            name="ck_quiz_library_entitlement_status",
        ),
        CheckConstraint(
            "source_type IN ('course_order', 'course_enrollment')",
            name="ck_quiz_library_entitlement_source_type",
        ),
        CheckConstraint(
            "((source_type = 'course_order' AND order_id IS NOT NULL) OR "
            "(source_type = 'course_enrollment' AND enrollment_id IS NOT NULL))",
            name="ck_quiz_library_entitlement_source_ref",
        ),
        CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name="ck_quiz_library_entitlement_period",
        ),
        UniqueConstraint(
            "order_id",
            "library_id",
            name="uq_quiz_library_entitlement_order_library",
        ),
        Index(
            "uq_quiz_library_entitlement_enrollment_library",
            "enrollment_id",
            "library_id",
            unique=True,
            postgresql_where=text("enrollment_id IS NOT NULL"),
            sqlite_where=text("enrollment_id IS NOT NULL"),
        ),
        Index(
            "ix_quiz_library_entitlement_access",
            "user_id",
            "library_id",
            "status",
            "starts_at",
            "ends_at",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    library_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_library.id", ondelete="RESTRICT"), nullable=False
    )
    course_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("course.id", ondelete="RESTRICT"), nullable=False
    )
    order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("order.id", ondelete="RESTRICT"), nullable=True
    )
    enrollment_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("course_enrollment.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default="course_order", server_default="course_order"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class QuizLegacyMigrationMap(Base, _QuizCreatedAtMixin):
    """Permanent, idempotent mapping from the old recursive model to V2."""

    __tablename__ = "quiz_legacy_migration_map"
    __table_args__ = (
        CheckConstraint(
            "legacy_object_type IN ('category', 'question')",
            name="ck_quiz_legacy_migration_object_type",
        ),
        CheckConstraint(
            "target_object_type IN ('library', 'module', 'knowledge_point', 'question')",
            name="ck_quiz_legacy_migration_target_type",
        ),
        CheckConstraint(
            "migration_status IN ('mapped', 'quarantined')",
            name="ck_quiz_legacy_migration_status",
        ),
        UniqueConstraint(
            "legacy_object_type", "legacy_id", name="uq_quiz_legacy_migration_source"
        ),
        Index("ix_quiz_legacy_migration_target", "target_object_type", "target_id"),
    )

    legacy_object_type: Mapped[str] = mapped_column(String(24), nullable=False)
    legacy_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_object_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    library_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_library.id", ondelete="RESTRICT"), nullable=False
    )
    migration_status: Mapped[str] = mapped_column(String(24), nullable=False)
    original_path: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    issue_code: Mapped[str | None] = mapped_column(String(64))


class QuizMigrationIssue(Base, _QuizCreatedAtMixin):
    __tablename__ = "quiz_migration_issue"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('warning', 'blocking')",
            name="ck_quiz_migration_issue_severity",
        ),
        CheckConstraint(
            "legacy_object_type IN ('category', 'question')",
            name="ck_quiz_migration_issue_object_type",
        ),
        CheckConstraint(
            "status IN ('open', 'resolved')",
            name="ck_quiz_migration_issue_status",
        ),
        UniqueConstraint(
            "issue_code",
            "legacy_object_type",
            "legacy_id",
            name="uq_quiz_migration_issue_source",
        ),
        Index("ix_quiz_migration_issue_library", "library_id", "status", "id"),
    )

    library_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_library.id", ondelete="RESTRICT"), nullable=False
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", server_default="open"
    )
    issue_code: Mapped[str] = mapped_column(String(64), nullable=False)
    legacy_object_type: Mapped[str] = mapped_column(String(24), nullable=False)
    legacy_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_path: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    resolution: Mapped[str] = mapped_column(String(256), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuizPracticeSession(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_practice_session"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('normal', 'wrong', 'full', 'wrong_only', 'legacy_limited')",
            name="ck_quiz_practice_session_mode",
        ),
        CheckConstraint(
            "status IN ('in_progress', 'paused', 'completed', 'abandoned', "
            "'expired', 'terminated')",
            name="ck_quiz_practice_session_status",
        ),
        CheckConstraint(
            "((mode = 'normal' AND category_id IS NOT NULL "
            "AND requested_count BETWEEN 10 AND 100) OR "
            "(mode = 'wrong' AND category_id IS NULL AND requested_count = 20) OR "
            "(mode = 'legacy_limited' AND requested_count BETWEEN 1 AND 100) OR "
            "(mode IN ('full', 'wrong_only') AND scope_type IS NOT NULL "
            "AND scope_id IS NOT NULL AND requested_count >= 1))",
            name="ck_quiz_practice_session_request",
        ),
        CheckConstraint(
            "actual_count >= 1",
            name="ck_quiz_practice_session_actual_count",
        ),
        CheckConstraint(
            "((status IN ('in_progress', 'paused') AND completed_at IS NULL "
            "AND abandoned_at IS NULL AND terminated_at IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND abandoned_at IS NULL) OR "
            "(status = 'abandoned' AND completed_at IS NULL AND abandoned_at IS NOT NULL) OR "
            "(status = 'expired' AND expired_at IS NOT NULL) OR "
            "(status = 'terminated' AND terminated_at IS NOT NULL))",
            name="ck_quiz_practice_session_lifecycle",
        ),
        CheckConstraint(
            "scope_type IS NULL OR scope_type IN ('library', 'module', 'knowledge_point')",
            name="ck_quiz_practice_session_scope_type",
        ),
        CheckConstraint(
            "paused_seconds >= 0", name="ck_quiz_practice_session_paused_seconds"
        ),
        CheckConstraint(
            "lock_version >= 1", name="ck_quiz_practice_session_lock_version"
        ),
        Index(
            "uq_quiz_practice_session_active_user",
            "user_id",
            unique=True,
            postgresql_where=text(
                "status = 'in_progress' AND scope_type IS NULL"
            ),
            sqlite_where=text("status = 'in_progress' AND scope_type IS NULL"),
        ),
        Index("ix_quiz_practice_session_user_history", "user_id", "started_at", "id"),
        Index(
            "uq_quiz_practice_session_active_scope",
            "user_id",
            "scope_type",
            "scope_id",
            "mode",
            unique=True,
            postgresql_where=text(
                "status IN ('in_progress', 'paused') AND scope_type IS NOT NULL"
            ),
            sqlite_where=text(
                "status IN ('in_progress', 'paused') AND scope_type IS NOT NULL"
            ),
        ),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("quiz_category.id", ondelete="RESTRICT")
    )
    library_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("quiz_library.id", ondelete="RESTRICT")
    )
    scope_type: Mapped[str | None] = mapped_column(String(24))
    scope_id: Mapped[int | None] = mapped_column(BigInteger)
    requested_count: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="in_progress", server_default="in_progress"
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pause_reason: Mapped[str | None] = mapped_column(String(64))
    paused_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    termination_reason: Mapped[str | None] = mapped_column(String(64))
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


class QuizPracticeSessionQuestion(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_practice_session_question"
    __table_args__ = (
        CheckConstraint("position >= 1", name="ck_quiz_practice_question_position"),
        CheckConstraint(
            "question_type IN ('single_choice', 'multiple_choice', 'judge')",
            name="ck_quiz_practice_question_type",
        ),
        CheckConstraint(
            "skip_count BETWEEN 0 AND 1", name="ck_quiz_practice_question_skip_count"
        ),
        CheckConstraint(
            "((user_answer IS NULL AND answer_lock_version = 0 "
            "AND answer_saved_at IS NULL) OR "
            "(user_answer IS NOT NULL AND answer_lock_version >= 1 "
            "AND answer_saved_at IS NOT NULL))",
            name="ck_quiz_practice_question_saved_answer",
        ),
        UniqueConstraint(
            "session_id", "position", name="uq_quiz_practice_question_position"
        ),
        UniqueConstraint(
            "session_id", "question_id", name="uq_quiz_practice_question_question"
        ),
        UniqueConstraint(
            "session_id", "id", name="uq_quiz_practice_question_session_id"
        ),
        Index("ix_quiz_practice_question_session", "session_id", "position"),
    )

    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_practice_session.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_question.id", ondelete="RESTRICT"), nullable=False
    )
    question_revision_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("quiz_question_revision.id", ondelete="RESTRICT")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    category_id: Mapped[int | None] = mapped_column(BigInteger)
    category_path: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    question_type: Mapped[str] = mapped_column(String(24), nullable=False)
    question_text: Mapped[str] = mapped_column(String(1024), nullable=False)
    options: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    correct_answer: Mapped[str | list[str]] = mapped_column(JSONB, nullable=False)
    explanation: Mapped[str] = mapped_column(String(1024), nullable=False)
    question_lock_version: Mapped[int] = mapped_column(Integer, nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    skip_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    user_answer: Mapped[str | list[str] | None] = mapped_column(JSONB)
    answer_lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    answer_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuizPracticeAttempt(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_practice_attempt"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "session_question_id"],
            [
                "quiz_practice_session_question.session_id",
                "quiz_practice_session_question.id",
            ],
            name="fk_quiz_practice_attempt_session_question",
            ondelete="CASCADE",
        ),
        CheckConstraint("attempt_no >= 1", name="ck_quiz_practice_attempt_no"),
        CheckConstraint(
            "is_first_attempt = (attempt_no = 1)",
            name="ck_quiz_practice_attempt_first",
        ),
        UniqueConstraint(
            "user_id",
            "session_id",
            "idempotency_key",
            name="uq_quiz_practice_attempt_idempotency",
        ),
        UniqueConstraint(
            "session_question_id",
            "attempt_no",
            name="uq_quiz_practice_attempt_number",
        ),
        Index("ix_quiz_practice_attempt_user_time", "user_id", "submitted_at", "id"),
        Index("ix_quiz_practice_attempt_submitted", "submitted_at", "id"),
        Index(
            "ix_quiz_practice_attempt_session_question",
            "session_id",
            "session_question_id",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    session_question_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    is_first_attempt: Mapped[bool] = mapped_column(Boolean, nullable=False)
    user_answer: Mapped[str | list[str]] = mapped_column(JSONB, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuizWrongItem(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_wrong_item"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'cleared')", name="ck_quiz_wrong_item_status"
        ),
        CheckConstraint(
            "(status = 'active' OR "
            "(status = 'cleared' AND cleared_at IS NOT NULL))",
            name="ck_quiz_wrong_item_lifecycle",
        ),
        CheckConstraint(
            "review_count >= 0", name="ck_quiz_wrong_item_review_count"
        ),
        UniqueConstraint("user_id", "question_id", name="uq_quiz_wrong_item_user_question"),
        Index("ix_quiz_wrong_item_recent", "user_id", "status", "latest_wrong_at", "id"),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_question.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    first_wrong_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latest_wrong_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_wrong_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    review_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuizQuestionRevisionStats(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_question_revision_stats"
    __table_args__ = (
        UniqueConstraint(
            "question_revision_id", name="uq_quiz_question_revision_stats_revision"
        ),
        CheckConstraint(
            "practice_first_attempts >= 0 AND practice_first_correct >= 0 "
            "AND exam_answers >= 0 AND exam_correct >= 0 "
            "AND practice_first_correct <= practice_first_attempts "
            "AND exam_correct <= exam_answers",
            name="ck_quiz_question_revision_stats_nonnegative",
        ),
    )

    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_question.id", ondelete="RESTRICT"), nullable=False
    )
    question_revision_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_question_revision.id", ondelete="CASCADE"),
        nullable=False,
    )
    practice_first_attempts: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    practice_first_correct: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    exam_answers: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    exam_correct: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    aggregated_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuizCollection(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_collection"
    __table_args__ = (
        UniqueConstraint("user_id", "question_id", name="uq_quiz_collection_user_question"),
        CheckConstraint(
            "(is_active = true OR removed_at IS NOT NULL)",
            name="ck_quiz_collection_lifecycle",
        ),
        Index("ix_quiz_collection_active", "user_id", "is_active", "collected_at", "id"),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_question.id", ondelete="RESTRICT"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuizCheckin(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_checkin"
    __table_args__ = (
        CheckConstraint(
            "questions_completed >= 1", name="ck_quiz_checkin_questions_completed"
        ),
        CheckConstraint("consecutive_days >= 1", name="ck_quiz_checkin_consecutive_days"),
        UniqueConstraint("user_id", "checkin_date", name="uq_quiz_checkin_user_date"),
        UniqueConstraint("first_attempt_id", name="uq_quiz_checkin_first_attempt"),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    checkin_date: Mapped[date] = mapped_column(Date, nullable=False)
    questions_completed: Mapped[int] = mapped_column(Integer, nullable=False)
    consecutive_days: Mapped[int] = mapped_column(Integer, nullable=False)
    first_attempt_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_practice_attempt.id", ondelete="CASCADE"),
        nullable=False,
    )


class QuizExam(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_exam"
    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress', 'completed', 'timed_out', 'abandoned')",
            name="ck_quiz_exam_status",
        ),
        CheckConstraint(
            "scope_type IS NULL OR scope_type IN "
            "('library', 'module', 'knowledge_point')",
            name="ck_quiz_exam_scope_type",
        ),
        CheckConstraint(
            "((scope_type IS NULL AND scope_id IS NULL AND library_id IS NULL "
            "AND category_id IS NOT NULL) OR "
            "(scope_type IS NOT NULL AND scope_id IS NOT NULL "
            "AND library_id IS NOT NULL AND category_id IS NULL))",
            name="ck_quiz_exam_scope",
        ),
        CheckConstraint(
            "question_count BETWEEN 10 AND 100", name="ck_quiz_exam_question_count"
        ),
        CheckConstraint("duration_seconds = 3600", name="ck_quiz_exam_duration"),
        CheckConstraint(
            "deadline_at = started_at + INTERVAL '3600 seconds'",
            name="ck_quiz_exam_deadline",
        ),
        CheckConstraint("lock_version >= 1", name="ck_quiz_exam_lock_version"),
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 100)",
            name="ck_quiz_exam_score",
        ),
        CheckConstraint(
            "((status IN ('in_progress', 'abandoned') AND correct_count IS NULL "
            "AND wrong_count IS NULL AND unanswered_count IS NULL AND score IS NULL) OR "
            "(status IN ('completed', 'timed_out') AND correct_count IS NOT NULL "
            "AND wrong_count IS NOT NULL AND unanswered_count IS NOT NULL "
            "AND score IS NOT NULL AND correct_count >= 0 AND wrong_count >= 0 "
            "AND unanswered_count >= 0 AND "
            "correct_count + wrong_count + unanswered_count = question_count))",
            name="ck_quiz_exam_result_state",
        ),
        CheckConstraint(
            "((status = 'in_progress' AND submitted_at IS NULL AND timed_out_at IS NULL "
            "AND abandoned_at IS NULL) OR "
            "(status = 'completed' AND submitted_at IS NOT NULL AND timed_out_at IS NULL "
            "AND abandoned_at IS NULL) OR "
            "(status = 'timed_out' AND submitted_at IS NULL AND timed_out_at IS NOT NULL "
            "AND abandoned_at IS NULL) OR "
            "(status = 'abandoned' AND submitted_at IS NULL AND timed_out_at IS NULL "
            "AND abandoned_at IS NOT NULL))",
            name="ck_quiz_exam_lifecycle",
        ),
        Index(
            "uq_quiz_exam_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'in_progress'"),
            sqlite_where=text("status = 'in_progress'"),
        ),
        Index("ix_quiz_exam_deadline", "status", "deadline_at", "id"),
        Index("ix_quiz_exam_status_updated", "status", "updated_at", "id"),
        Index("ix_quiz_exam_user_history", "user_id", "started_at", "id"),
        Index(
            "ix_quiz_exam_scope_history",
            "user_id",
            "library_id",
            "scope_type",
            "scope_id",
            "started_at",
            "id",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("quiz_category.id", ondelete="RESTRICT"),
        nullable=True,
    )
    library_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("quiz_library.id", ondelete="RESTRICT")
    )
    scope_type: Mapped[str | None] = mapped_column(String(24))
    scope_id: Mapped[int | None] = mapped_column(BigInteger)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3600, server_default="3600"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="in_progress", server_default="in_progress"
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timed_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correct_count: Mapped[int | None] = mapped_column(Integer)
    wrong_count: Mapped[int | None] = mapped_column(Integer)
    unanswered_count: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


class QuizExamQuestion(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_exam_question"
    __table_args__ = (
        CheckConstraint("position >= 1", name="ck_quiz_exam_question_position"),
        CheckConstraint(
            "question_type IN ('single_choice', 'multiple_choice', 'judge')",
            name="ck_quiz_exam_question_type",
        ),
        UniqueConstraint("exam_id", "position", name="uq_quiz_exam_question_position"),
        UniqueConstraint("exam_id", "question_id", name="uq_quiz_exam_question_question"),
        UniqueConstraint("exam_id", "id", name="uq_quiz_exam_question_exam_id"),
        ForeignKeyConstraint(
            ["question_revision_id", "question_id"],
            ["quiz_question_revision.id", "quiz_question_revision.question_id"],
            name="fk_quiz_exam_question_revision_question",
            ondelete="RESTRICT",
        ),
        Index("ix_quiz_exam_question_exam", "exam_id", "position"),
    )

    exam_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_exam.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_question.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    question_revision_id: Mapped[int | None] = mapped_column(
        BigInteger
    )
    category_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    category_path: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    question_type: Mapped[str] = mapped_column(String(24), nullable=False)
    question_text: Mapped[str] = mapped_column(String(1024), nullable=False)
    options: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    correct_answer: Mapped[str | list[str]] = mapped_column(JSONB, nullable=False)
    explanation: Mapped[str] = mapped_column(String(1024), nullable=False)
    question_lock_version: Mapped[int] = mapped_column(Integer, nullable=False)


class QuizExamAnswer(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_exam_answer"
    __table_args__ = (
        ForeignKeyConstraint(
            ["exam_id", "exam_question_id"],
            ["quiz_exam_question.exam_id", "quiz_exam_question.id"],
            name="fk_quiz_exam_answer_exam_question",
            ondelete="CASCADE",
        ),
        CheckConstraint("lock_version >= 1", name="ck_quiz_exam_answer_lock_version"),
        UniqueConstraint("exam_question_id", name="uq_quiz_exam_answer_question"),
        Index("ix_quiz_exam_answer_exam", "exam_id", "exam_question_id"),
        Index("ix_quiz_exam_answer_updated", "updated_at", "id"),
    )

    exam_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    exam_question_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_answer: Mapped[str | list[str]] = mapped_column(JSONB, nullable=False)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


class QuizUserStats(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_user_stats"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_quiz_user_stats_user"),
        CheckConstraint(
            "practice_total_attempts >= 0 AND practice_first_attempts >= 0 "
            "AND practice_first_correct >= 0 AND practice_answered_questions >= 0 "
            "AND active_wrong_count >= 0 AND active_collection_count >= 0 "
            "AND checkin_days >= 0 AND consecutive_days >= 0 "
            "AND today_practice_count >= 0 AND completed_exam_count >= 0 "
            "AND timed_out_exam_count >= 0 AND exam_total_questions >= 0 "
            "AND exam_correct >= 0 AND exam_wrong >= 0 AND exam_unanswered >= 0 "
            "AND exam_score_sum >= 0 AND practice_first_correct <= practice_first_attempts "
            "AND exam_correct + exam_wrong + exam_unanswered = exam_total_questions "
            "AND (exam_high_score IS NULL OR (exam_high_score >= 0 AND exam_high_score <= 100)) "
            "AND (exam_latest_score IS NULL OR (exam_latest_score >= 0 AND exam_latest_score <= 100))",
            name="ck_quiz_user_stats_nonnegative",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    practice_total_attempts: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    practice_first_attempts: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    practice_first_correct: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    practice_answered_questions: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    active_wrong_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    active_collection_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    checkin_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    consecutive_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    today_practice_date: Mapped[date | None] = mapped_column(Date)
    today_practice_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    completed_exam_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    timed_out_exam_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    exam_total_questions: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    exam_correct: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    exam_wrong: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    exam_unanswered: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    exam_score_sum: Mapped[Decimal] = mapped_column(
        Numeric(14, 1), nullable=False, default=0, server_default="0"
    )
    exam_high_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    exam_latest_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    exam_latest_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuizQuestionStats(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_question_stats"
    __table_args__ = (
        UniqueConstraint("question_id", name="uq_quiz_question_stats_question"),
        CheckConstraint(
            "practice_first_attempts >= 0 AND practice_first_correct >= 0 "
            "AND exam_answers >= 0 AND exam_correct >= 0 "
            "AND practice_first_correct <= practice_first_attempts "
            "AND exam_correct <= exam_answers",
            name="ck_quiz_question_stats_nonnegative",
        ),
    )

    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_question.id", ondelete="CASCADE"), nullable=False
    )
    practice_first_attempts: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    practice_first_correct: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    exam_answers: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    exam_correct: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    aggregated_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuizImportJob(Base, _QuizTimestampMixin):
    __tablename__ = "quiz_import_job"
    __table_args__ = (
        CheckConstraint("source_type IN ('csv', 'json')", name="ck_quiz_import_source_type"),
        CheckConstraint(
            "status IN ('queued', 'validating', 'validation_failed', 'importing', "
            "'awaiting_category_confirmation', 'succeeded', 'failed', "
            "'cancelled', 'expired')",
            name="ck_quiz_import_status",
        ),
        CheckConstraint(
            "source_size_bytes BETWEEN 1 AND 10485760",
            name="ck_quiz_import_source_size",
        ),
        CheckConstraint(
            "total_rows BETWEEN 0 AND 5000 AND validated_rows BETWEEN 0 AND 5000 "
            "AND created_count BETWEEN 0 AND 5000 AND error_count >= 0 "
            "AND validated_rows <= total_rows AND created_count <= total_rows",
            name="ck_quiz_import_counts",
        ),
        CheckConstraint("retry_count >= 0", name="ck_quiz_import_retry_count"),
        CheckConstraint("lock_version >= 1", name="ck_quiz_import_lock_version"),
        CheckConstraint(
            "validation_version >= 0", name="ck_quiz_import_validation_version"
        ),
        CheckConstraint(
            "missing_category_count BETWEEN 0 AND 500 "
            "AND affected_question_count BETWEEN 0 AND 5000",
            name="ck_quiz_import_category_counts",
        ),
        UniqueConstraint("import_batch_key", name="uq_quiz_import_batch_key"),
        Index("ix_quiz_import_job_queue", "status", "created_at", "id"),
        Index("ix_quiz_import_job_admin", "admin_id", "created_at", "id"),
        Index("ix_quiz_import_job_expiry", "expires_at", "id"),
    )

    admin_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )
    library_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("quiz_library.id", ondelete="RESTRICT")
    )
    import_batch_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", server_default="queued"
    )
    source_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    source_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    report_object_key: Mapped[str | None] = mapped_column(String(512))
    total_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    validated_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_message: Mapped[str | None] = mapped_column(String(1024))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    validation_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    impact_version: Mapped[str | None] = mapped_column(String(64))
    category_impact: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    missing_category_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    affected_question_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    confirmed_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="SET NULL")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_protected_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    @property
    def report_available(self) -> bool:
        # Retention is enforced at the model boundary as well as by the
        # download service, so expired jobs never advertise a usable report.
        expires_at = self.expires_at
        if expires_at is None:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return bool(self.report_object_key) and expires_at > datetime.now(timezone.utc)


class QuizImportError(Base, _QuizCreatedAtMixin):
    __tablename__ = "quiz_import_error"
    __table_args__ = (
        CheckConstraint('"row" IS NULL OR "row" >= 1', name="ck_quiz_import_error_row"),
        CheckConstraint(
            "question_index IS NULL OR question_index >= 1",
            name="ck_quiz_import_error_question_index",
        ),
        Index("ix_quiz_import_error_page", "job_id", "validation_version", "id"),
        Index(
            "ix_quiz_import_error_field",
            "job_id",
            "validation_version",
            "field",
            "id",
        ),
    )

    job_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quiz_import_job.id", ondelete="CASCADE"),
        nullable=False,
    )
    validation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    row: Mapped[int | None] = mapped_column(Integer)
    question_index: Mapped[int | None] = mapped_column(Integer)
    field: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(String(1024), nullable=False)


class QuizAdminAuditLog(Base, _QuizCreatedAtMixin):
    __tablename__ = "quiz_admin_audit_log"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('admin', 'system')", name="ck_quiz_audit_actor_type"
        ),
        CheckConstraint(
            "result IN ('succeeded', 'failed')", name="ck_quiz_audit_result"
        ),
        CheckConstraint(
            "((actor_type = 'admin' AND admin_id IS NOT NULL) OR "
            "(actor_type = 'system' AND admin_id IS NULL))",
            name="ck_quiz_audit_actor",
        ),
        Index("ix_quiz_audit_object", "object_type", "object_id", "created_at"),
        Index("ix_quiz_audit_actor", "actor_type", "admin_id", "created_at"),
        Index("ix_quiz_audit_action", "action", "created_at"),
    )

    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    admin_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("admin_user.id", ondelete="RESTRICT")
    )
    permission: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[int | None] = mapped_column(BigInteger)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    changed_fields: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    target_ids: Mapped[list[int] | None] = mapped_column(JSONB)
    error_summary: Mapped[str | None] = mapped_column(Text)


def _sanitize_quiz_audit(target: QuizAdminAuditLog) -> None:
    from app.utils.audit import redact_sensitive_text, sanitize_audit_value

    target.changed_fields = sanitize_audit_value(target.changed_fields)
    target.target_ids = sanitize_audit_value(target.target_ids)
    target.error_summary = redact_sensitive_text(target.error_summary)


@event.listens_for(QuizAdminAuditLog, "before_insert")
def _sanitize_quiz_audit_before_insert(_mapper, _connection, target) -> None:
    """Enforce the permanent audit redaction boundary before persistence."""

    _sanitize_quiz_audit(target)


@event.listens_for(QuizAdminAuditLog, "before_update")
def _reject_quiz_audit_update(_mapper, _connection, _target) -> None:
    """Quiz audit rows are append-only even for direct ORM callers."""

    raise ValueError("quiz audit logs are immutable")


__all__ = [
    "QuizAdminAuditLog",
    "QuizCategory",
    "QuizCheckin",
    "QuizCollection",
    "QuizCourseLibraryBinding",
    "QuizExam",
    "QuizExamAnswer",
    "QuizExamQuestion",
    "QuizImportJob",
    "QuizImportError",
    "QuizKnowledgePoint",
    "QuizLegacyMigrationMap",
    "QuizLibrary",
    "QuizLibraryEntitlement",
    "QuizMigrationIssue",
    "QuizModule",
    "QuizPracticeAttempt",
    "QuizPracticeSession",
    "QuizPracticeSessionQuestion",
    "QuizQuestion",
    "QuizQuestionRevision",
    "QuizQuestionRevisionStats",
    "QuizQuestionStats",
    "QuizUserStats",
    "QuizWrongItem",
]
