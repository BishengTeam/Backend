"""人社多项目批次：统一解析报名/订单/导出所需项目属性。"""
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.domain.renshe.src.index import RensheBatchProject
from app.models.cert_product import CertProduct
from app.models.renshe_project import RensheProjectConfig
from app.port.exceptions import BusinessException
from app.port.exceptions import ConflictException


@dataclass(frozen=True)
class RensheProjectResolution:
    batch_project: RensheBatchProject | None
    config_id: int | None
    product_code: str | None
    occupation_name: str | None
    occupation_code: str | None
    skill_level: str | None
    price_cents: int
    is_active: bool | None

    @property
    def batch_project_id(self) -> int | None:
        return self.batch_project.id if self.batch_project else None


async def resolve_batch_project_id(
    db: AsyncSession, plan_id: int, batch_project_id: int | None
) -> RensheBatchProject | None:
    if not batch_project_id:
        return None
    project = await db.get(RensheBatchProject, batch_project_id)
    if project is None:
        raise BusinessException("报考项目不存在")
    if project.plan_id != plan_id:
        raise BusinessException("报考项目不属于当前批次")
    return project


async def resolve_project(
    db: AsyncSession,
    plan,
    *,
    batch_project_id: int | None = None,
    require_active: bool = False,
) -> RensheProjectResolution:
    project = await resolve_batch_project_id(db, plan.id, batch_project_id)
    if require_active and project is not None and not project.is_active:
        raise BusinessException("报考项目已停用")
    product_code = plan.product_type
    if project is not None:
        product = await db.get(CertProduct, project.product_id)
        product_code = product.code if product else product_code
    return RensheProjectResolution(
        batch_project=project,
        config_id=project.config_id if project else plan.renshe_config_id,
        product_code=product_code,
        occupation_name=(
            project.occupation_name if project else plan.occupation_name
        ),
        occupation_code=(
            project.occupation_code if project else plan.occupation_code
        ),
        skill_level=project.skill_level if project else plan.skill_level,
        price_cents=project.price_cents if project else plan.price_cents,
        is_active=project.is_active if project else None,
    )


async def resolve_application_project(db: AsyncSession, application, plan):
    return await resolve_project(
        db, plan, batch_project_id=getattr(application, "batch_project_id", None)
    )


async def resolve_existing_project(db: AsyncSession, application, plan):
    """历史报名/审核/导出：允许已停用项目。"""
    return await resolve_application_project(db, application, plan)


async def resolve_enrollable_project(
    db: AsyncSession, plan, batch_project_id: int | None
) -> RensheProjectResolution:
    """新报名：项目必须存在、属于当前批次且处于启用状态。"""
    all_projects = list((await db.scalars(
        select(RensheBatchProject).where(
            RensheBatchProject.plan_id == plan.id,
        )
    )).all())
    if not all_projects and (
        getattr(plan, "product_type", None) is None
        or getattr(plan, "renshe_config_id", None) is None
    ):
        raise BusinessException("本批次尚未配置报考项目")
    active_projects = [item for item in all_projects if item.is_active]
    if all_projects and not active_projects and batch_project_id is None:
        raise BusinessException("当前批次暂无可用报考项目")
    if active_projects and batch_project_id is None:
        if len(active_projects) > 1:
            raise BusinessException("请选择报考项目")
        batch_project_id = active_projects[0].id

    resolved = await resolve_project(
        db, plan, batch_project_id=batch_project_id, require_active=True
    )
    project = resolved.batch_project
    if project is None:
        return resolved

    product = await db.get(CertProduct, project.product_id)
    if product is None or product.type != "renshe":
        raise BusinessException("报考项目对应的认证产品无效")
    if not product.is_active:
        raise BusinessException("报考项目对应产品已下架")
    if project.config_id is not None:
        config = await db.get(RensheProjectConfig, project.config_id)
        if config is None or config.product_id != project.product_id:
            raise BusinessException("报考项目配置不属于该项目")
    return resolved


def select_write_application(
    applications: list,
    *,
    explicit_project_id: int | None,
    resolved_project_id: int | None,
):
    """为新报名选择应复用的活跃报名；返回 (application, 是否补写 legacy 项目)。"""
    if len(applications) > 1:
        raise ConflictException("存在多条活跃报名，请联系管理员整理后再报名")
    if applications:
        application = applications[0]
        if application.batch_project_id is None:
            if explicit_project_id is not None:
                raise ConflictException(
                    "该批次存在未关联项目的历史报名，请联系管理员处理"
                )
            if resolved_project_id is not None:
                return application, True
            return application, False
        if (
            resolved_project_id is not None
            and application.batch_project_id != resolved_project_id
        ):
            raise ConflictException("该批次已有其他项目的报名，不能重复报名")
        return application, False
    return None, False


def resolve_upload_project_id(applications: list, provided_project_id: int | None):
    """材料上传：已有报名以报名项目为准，否则使用传入项目。"""
    if len(applications) > 1:
        raise ConflictException("存在多条活跃报名，请联系管理员整理后再上传材料")
    if applications:
        registered = applications[0].batch_project_id
        if provided_project_id is not None and registered is not None and provided_project_id != registered:
            raise ConflictException("上传材料与报名项目不一致")
        return registered if registered is not None else provided_project_id
    return provided_project_id
