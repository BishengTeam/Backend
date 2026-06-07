"""统一种子数据编排入口

用法：
    python scripts/seed_all.py              # 幂等：每步独立检查，有则跳过
    python scripts/seed_all.py --force       # 强制重建（truncate 后重新 seed）
    python scripts/seed_all.py --skip-admin  # 跳过管理员创建
    python scripts/seed_all.py --skip-config # 跳过基础配置
    python scripts/seed_all.py --skip-testdata # 跳过测试数据

数据依赖层次（自底向上）：
    第 1 层  seed_admin    — 5 角色管理员
    第 1 层  seed_config   — 认证 / 价格 / 题库分类
    第 2-4 层 seed_testdata — 用户 / 订单 / 积分 / 课程 / 优惠券 / Banner / Zone
"""

import asyncio
import importlib
import os
import sys
import traceback

# 确保 Backend 项目根目录在 sys.path 中（支持本地和容器内运行）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 注入缺失的环境变量，避免 Settings() 初始化失败。
# 脚本只需要数据库连接，不需要 JWT / 微信等业务配置。
os.environ.setdefault("JWT_SECRET", "seed-script-placeholder-do-not-use-in-prod")


def print_step(msg: str):
    print(f"\n{'─' * 60}")
    print(f"  {msg}")
    print(f"{'─' * 60}")


async def main():
    force = "--force" in sys.argv
    skip_admin = "--skip-admin" in sys.argv
    skip_config = "--skip-config" in sys.argv
    skip_testdata = "--skip-testdata" in sys.argv

    success = True

    # ── 第 1 步：管理员 ─────────────────────────────────
    if not skip_admin:
        print_step("第 1 步: 管理员账号")
        try:
            seed_admin_mod = importlib.import_module("scripts.seed_admin")
            await seed_admin_mod.main()
        except Exception:
            traceback.print_exc()
            print("  ❌ 管理员创建失败")
            success = False

    # ── 第 2 步：基础配置 ───────────────────────────────
    if not skip_config:
        print_step("第 2 步: 基础配置")
        try:
            seed_config_mod = importlib.import_module("scripts.seed_data")
            await seed_config_mod.main()
        except Exception:
            traceback.print_exc()
            print("  ❌ 基础配置初始化失败")
            success = False

    # ── 第 3 步：测试数据 ───────────────────────────────
    if not skip_testdata:
        print_step("第 3 步: 测试数据")
        try:
            # seed_testdata 直接从 sys.argv 读取 --force 参数
            seed_testdata_mod = importlib.import_module("scripts.seed_testdata")
            await seed_testdata_mod.main()
        except Exception:
            traceback.print_exc()
            print("  ❌ 测试数据初始化失败")
            success = False

    # ── 总结 ───────────────────────────────────────────
    print(f"\n{'═' * 60}")
    if success:
        print("  ✅ 全部种子数据初始化完成")
    else:
        print("  ⚠ 部分步骤失败，请检查上方错误信息")
    print(f"{'═' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
