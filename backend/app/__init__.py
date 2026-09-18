"""跨境电商 AI 运营工作台后端（交付层）。

**本包只做 Web 交付**：路由、依赖装配、静态托管、单容器入口。
业务逻辑在同仓 `packages/` 下的独立包里（数据中台、售前咨询、内容运营…），
共享能力在另一个仓库的 `ecom-agent-shared` 里。依赖方向单向：
交付层 → 业务包 → 共享集群。

下面这段是**开发态路径引导**：让 `import app` 在任何启动方式下（uvicorn、
pytest、scripts/）都能找到同仓 `packages/` 里的包，以及并联的共享包仓库。
已经 pip 安装过这些包的环境里，import 本来就通，引导逻辑不产生副作用。

为什么不靠 PYTHONPATH：`uvicorn app.main:app`、`pytest`、`python -m scripts.x`
三种启动方式的 sys.path 基线各不相同，靠环境变量维持一致最容易漏。
放在 `app/__init__.py` 里则必然先于任何业务 import 执行。
"""
from __future__ import annotations

import os
import sys

__version__ = "2.0.0"

#: 仓库根（backend/app/__init__.py → backend/app → backend → 仓库根）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PACKAGES_DIR = os.path.join(REPO_ROOT, "packages")

SHARED_REPO_NAME = "ecom-agent-shared"


def _add_local_packages() -> list[str]:
    """把 `packages/<name>/<name>/` 这类本地包加进 sys.path。

    约定：`packages/data_platform/data_platform/` —— 外层是仓库分包，
    内层是 python 包。这样每个分包都自成一个可安装、可单独测试的工程实体。
    """
    added: list[str] = []
    if not os.path.isdir(PACKAGES_DIR):
        return added
    for name in sorted(os.listdir(PACKAGES_DIR)):
        pkg_root = os.path.join(PACKAGES_DIR, name)
        if os.path.isdir(os.path.join(pkg_root, name)) and pkg_root not in sys.path:
            sys.path.insert(0, pkg_root)
            added.append(pkg_root)
    return added


def _locate_shared_repo() -> str | None:
    """找并联的共享集群仓库（`ecom-agent-shared`）。

    三种布局都认：
      · vendor 进本仓：`<repo>/vendor/ecom-agent-shared`
      · 与仓库同级：  `<repo>/../ecom-agent-shared`
      · 再上一级：    `<repo>/../../ecom-agent-shared`
    找不到就返回 None —— 那说明是 pip 安装过的环境，交给正常 import 报错。
    """
    for base in (
        os.path.join(REPO_ROOT, "vendor"),
        os.path.dirname(REPO_ROOT),
        os.path.dirname(os.path.dirname(REPO_ROOT)),
    ):
        candidate = os.path.join(base, SHARED_REPO_NAME)
        if os.path.isdir(os.path.join(candidate, "ecom_shared")):
            return candidate
    return None


def _bootstrap_paths() -> list[str]:
    added = _add_local_packages()
    if "ecom_shared" not in sys.modules:
        shared = _locate_shared_repo()
        if shared and shared not in sys.path:
            sys.path.insert(0, shared)
            added.append(shared)
    return added


#: 本次实际新增的搜索路径。便于排查"到底用的是哪一份包"。
LOCAL_PATHS = _bootstrap_paths()

del _bootstrap_paths, _add_local_packages, _locate_shared_repo
