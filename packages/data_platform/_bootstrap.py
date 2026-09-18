"""开发态引导：把本包与并联的共享包加进 sys.path。

**只服务于「clone 下来直接跑」，不随包发布。**
`pyproject.toml` 里 `packages` 是显式列表，这个模块不会被安装进 site-packages ——
所以它不会污染使用 `pip install ecom-data-platform` 的用户。

为什么需要它：本包依赖 `ecom_shared`，那是**另一个仓库**的包。
如果只把本包目录塞进 sys.path，clone 完直接 `python demo.py` 或 `pytest`
会看到 `ModuleNotFoundError: No module named 'ecom_shared'` ——
报错信息里完全没提"你还得先 clone 那个仓"，是最容易劝退人的一种失败。

同时覆盖两种布局：
  · 工作台 monorepo：<repo>/packages/data_platform/
  · 独立仓库：      <repo>/data_platform/
"""
from __future__ import annotations

import os
import sys

SHARED_REPO_NAME = "ecom-agent-shared"
_SHARED_PKG_DIR = "ecom_shared"


def _locate_shared_repo(start: str, max_up: int = 4) -> str | None:
    """向上逐级找并联的共享包仓库。找不到返回 None，交给正常的 ImportError。"""
    current = start
    for _ in range(max_up):
        candidate = os.path.join(os.path.dirname(current), SHARED_REPO_NAME)
        if os.path.isdir(os.path.join(candidate, _SHARED_PKG_DIR)):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def ensure_paths(start: str | None = None) -> list[str]:
    """把 pyproject 里的包目录与共享包仓库加进 sys.path，返回本次新增的路径。

    `start` 传调用方所在目录（`os.path.dirname(os.path.abspath(__file__))`），
    这样无论从哪个工作目录调用都能正确定位。
    """
    here = start or os.path.dirname(os.path.abspath(__file__))
    added: list[str] = []
    for path in (here, os.path.join(here, "packages", "data_platform")):
        if path not in sys.path:
            sys.path.insert(0, path)
            added.append(path)

    if _SHARED_PKG_DIR not in sys.modules:
        shared = _locate_shared_repo(here)
        if shared and shared not in sys.path:
            sys.path.insert(0, shared)
            added.append(shared)
    return added
