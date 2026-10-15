"""Multi-project admission policies; migrate only confirmed legacy batches.

Revision ID: rsh003_project_versions
Revises: cp014_merge_cert_heads
"""

import logging

from alembic import op
import sqlalchemy as sa

revision = "rsh003_project_versions"
down_revision = "cp014_merge_cert_heads"
branch_labels = None
depends_on = None


def legacy_config():
    names = {
        "id_card_front": "身份证正面",
        "id_card_back": "身份证背面",
        "portrait": "二寸免冠照片",
        "student_card": "学生证",
        "xuexin_registration": "学信网电子注册表",
        "education_proof": "学历证明",
    }
    return {
        "occupation_name": "信息安全管理员",
        "occupation_code": "4-04-04-02-网络与信息安全管理员-信息安全管理员-中级工",
        "skill_level": "中级工",
        "export_skill_level": "四级",
        "exam_types": ["新考"],
        "application_types": ["学历型"],
        "qualification_rule": "related_major_current_graduating_student",
        "education_levels": [
            "associate",
            "bachelor",
            "doctorate",
            "master",
            "secondary_vocational",
        ],
        "form_fields": ["contact_phone", "mailing_address", "email"],
        "materials": [
            {
                "kind": kind,
                "name": name,
                "required": True,
                "extensions": [".pdf"]
                if kind == "xuexin_registration"
                else [".jpeg", ".jpg"],
                "max_count": 1,
            }
            for kind, name in names.items()
        ],
        "export_template": "renshe_registration_v1",
        "field_mapping": {},
        "attachment_layout": "by_candidate",
    }


def upgrade():
    op.add_column(
        "cert_product", sa.Column("renshe_status", sa.String(16), nullable=True)
    )
    op.create_table(
        "renshe_project_config",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Integer,
            sa.ForeignKey("cert_product.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("config", sa.JSON, nullable=False),
        sa.UniqueConstraint("product_id", "version", name="uq_renshe_project_version"),
    )
    op.create_index(
        "ix_renshe_project_config_product_id", "renshe_project_config", ["product_id"]
    )
    op.add_column(
        "plan",
        sa.Column(
            "renshe_config_id",
            sa.Integer,
            sa.ForeignKey(
                "renshe_project_config.id",
                name="fk_plan_renshe_config",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
    )
    op.add_column("plan", sa.Column("renshe_config", sa.JSON, nullable=True))
    op.add_column("plan", sa.Column("project_name", sa.String(128), nullable=True))
    op.add_column(
        "renshe_application_version",
        sa.Column(
            "project_config_id",
            sa.Integer,
            sa.ForeignKey(
                "renshe_project_config.id",
                name="fk_renshe_version_project_config",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "renshe_export_job",
        sa.Column(
            "project_config_id",
            sa.Integer,
            sa.ForeignKey(
                "renshe_project_config.id",
                name="fk_renshe_job_project_config",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
    )
    db = op.get_bind()
    # 离线 --sql 静态生成阶段没有可用连接，数据回填仅在真实升级时执行
    if op.get_context().as_sql:
        logging.getLogger("alembic.runtime.migration").warning(
            "offline SQL generation: skip rsh003 data backfill"
        )
        return
    product = (
        db.execute(
            sa.text(
                "SELECT id, chinese_name, is_active FROM cert_product WHERE code='RS-ZY' AND type='renshe'"
            )
        )
        .mappings()
        .first()
    )
    # Other products and uncertain batches are retained for explicit reconciliation.
    db.execute(
        sa.text(
            "UPDATE cert_product SET renshe_status='draft' WHERE type='renshe' AND code<>'RS-ZY'"
        )
    )
    if product is None:
        logging.getLogger("alembic.runtime.migration").warning(
            "RS-ZY 人社项目不存在，未自动归类历史批次"
        )
        return
    config = legacy_config()
    versions = sa.table(
        "renshe_project_config",
        sa.column("id", sa.Integer),
        sa.column("product_id", sa.Integer),
        sa.column("version", sa.Integer),
        sa.column("config", sa.JSON),
    )
    config_id = db.execute(
        sa.insert(versions)
        .values(product_id=product["id"], version=1, config=config)
        .returning(versions.c.id)
    ).scalar_one()
    db.execute(
        sa.text("UPDATE cert_product SET renshe_status=:status WHERE id=:id"),
        {
            "id": product["id"],
            "status": "available" if product["is_active"] else "disabled",
        },
    )
    rows = (
        db.execute(
            sa.text(
                "SELECT id, occupation_name, occupation_code, skill_level, exam_type, application_type FROM plan WHERE product_type='RS-ZY'"
            )
        )
        .mappings()
        .all()
    )
    uncertain = []
    bad_orders = (
        db.execute(
            sa.text("""
        SELECT a.plan_id, o.plan_id AS order_plan_id FROM "order" o
        JOIN renshe_application a ON a.id=o.application_id JOIN plan p ON p.id=a.plan_id
        WHERE o.user_id<>a.user_id OR o.plan_id IS NULL OR o.plan_id<>a.plan_id OR o.product_type<>p.product_type
    """)
        )
        .mappings()
        .all()
    )
    inconsistent = {
        item[key]
        for item in bad_orders
        for key in ("plan_id", "order_plan_id")
        if item[key] is not None
    }
    inconsistent.update(
        db.execute(
            sa.text("""
        SELECT j.plan_id FROM renshe_export_item i JOIN renshe_export_job j ON j.id=i.job_id
        JOIN renshe_application a ON a.id=i.application_id JOIN renshe_application_version v ON v.id=i.version_id
        WHERE a.plan_id<>j.plan_id OR v.application_id<>a.id
    """)
        )
        .scalars()
        .all()
    )
    inconsistent.update(
        db.execute(
            sa.text("""
        SELECT a.plan_id FROM renshe_refund_request r JOIN "order" o ON o.id=r.order_id
        JOIN renshe_application a ON a.id=r.application_id
        WHERE o.application_id IS NULL OR o.application_id<>a.id OR o.user_id<>a.user_id OR r.user_id<>a.user_id
           OR o.plan_id IS NULL OR o.plan_id<>a.plan_id OR o.price<>r.amount_cents
    """)
        )
        .scalars()
        .all()
    )
    plans = sa.table(
        "plan",
        sa.column("id", sa.Integer),
        sa.column("renshe_config_id", sa.Integer),
        sa.column("renshe_config", sa.JSON),
        sa.column("project_name", sa.String),
    )
    for row in rows:
        expected = {
            key: config[key]
            for key in ("occupation_name", "occupation_code", "skill_level")
        }
        expected.update(exam_type="新考", application_type="学历型")
        if row["id"] in inconsistent or any(
            row[key] != value for key, value in expected.items()
        ):
            uncertain.append(row["id"])
            continue
        db.execute(
            sa.update(plans)
            .where(plans.c.id == row["id"])
            .values(
                renshe_config_id=config_id,
                renshe_config=config,
                project_name=product["chinese_name"],
            )
        )
        db.execute(
            sa.text(
                "UPDATE renshe_application_version SET project_config_id=:config_id WHERE application_id IN (SELECT id FROM renshe_application WHERE plan_id=:plan_id)"
            ),
            {"config_id": config_id, "plan_id": row["id"]},
        )
        db.execute(
            sa.text(
                "UPDATE renshe_export_job SET project_config_id=:config_id WHERE plan_id=:plan_id"
            ),
            {"config_id": config_id, "plan_id": row["id"]},
        )
    if uncertain:
        logging.getLogger("alembic.runtime.migration").warning(
            "未确认归属的人社批次未迁移：%s", uncertain
        )


def downgrade():
    # Once another project has business data, rolling back to single-project
    # code loses its meaning; require a separately reviewed data rollback.
    db = op.get_bind()
    if db.scalar(
        sa.text(
            "SELECT count(*) FROM plan WHERE renshe_config_id IS NOT NULL AND product_type<>'RS-ZY'"
        )
    ):
        raise RuntimeError("已有新增项目批次，禁止直接回退到单项目模型")
    op.drop_constraint(
        "fk_renshe_version_project_config",
        "renshe_application_version",
        type_="foreignkey",
    )
    op.drop_column("renshe_application_version", "project_config_id")
    op.drop_constraint(
        "fk_renshe_job_project_config", "renshe_export_job", type_="foreignkey"
    )
    op.drop_column("renshe_export_job", "project_config_id")
    op.drop_constraint("fk_plan_renshe_config", "plan", type_="foreignkey")
    for name in ("project_name", "renshe_config", "renshe_config_id"):
        op.drop_column("plan", name)
    op.drop_table("renshe_project_config")
    op.drop_column("cert_product", "renshe_status")