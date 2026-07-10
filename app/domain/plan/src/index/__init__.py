"""domain/plan 公开入口。对外仅暴露此 index。"""

from app.domain.plan.src.model.plan import Plan

__all__ = ["Plan"]
