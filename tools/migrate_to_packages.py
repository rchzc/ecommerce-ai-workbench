"""一次性迁移脚本：把老的 backend/app 布局重组为 packages/ 下的独立包。

设计成脚本而不是手工搬文件，是因为这次要动 60 个文件、几十条相对导入 ——
手工改必然漏掉某条 `from ..core.xxx import`，而且要重复两次（②⑤⑦ 还要再来）。

脚本做三件事：
1. 按「包 → 文件」映射把文件复制到新位置；
2. 按规则重写相对导入（老布局的 `..config` → 新布局的 `.config`，共享能力改为 import ecom_shared）；
3. 打印一份"改了哪些导入"的清单，方便人工复核（不静默改）。

**只复制，不删除原文件。** 老的 backend/ 原样保留，直到全部包迁完并验证通过，
再单独提交一次删除。迁移期间两套并存是刻意的，出问题能立刻回退。
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass, field

REPO = r"D:\code\ecommerce-ai-workbench"
SRC = os.path.join(REPO, "backend", "app")


@dataclass
class Move:
    src: str                          # 相对 backend/app 的源路径
    dst: str                          # 相对 packages/<pkg> 的目标路径
    rewrites: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ③ 数据中台：数据接入 / 表格管道 / 批量任务 / 报表与看板 / 知识库运维
#
# 归属依据：这些代码的共同点是「围绕数据本身」——把外部数据拉进来、洗干净、
# 算指标、写回去，不产生面向用户的生成式回答。业务 Agent（②⑤⑦）消费它的产物。
# ---------------------------------------------------------------------------
DATA_PLATFORM: list[Move] = [
    # --- 连接器：外部系统接入适配层 ---
    Move("connectors/__init__.py", "connectors/__init__.py"),
    Move("connectors/amazon_sp_api.py", "connectors/amazon_sp_api.py"),
    Move("connectors/shopify.py", "connectors/shopify.py"),
    Move("connectors/feishu_bitable.py", "connectors/feishu_bitable.py"),
    # --- 表格与校验：批量数据的解析与规则兜底 ---
    Move("core/tabular.py", "tabular.py"),
    Move("core/validators.py", "validators.py"),
    # --- 三条数据链路 ---
    Move(
        "services/pipeline_service.py",
        "pipeline.py",
        [("from ..core.llm import", "from ecom_shared.gateway import")],
    ),
    Move(
        "services/batch_service.py",
        "batch.py",
        [],
    ),
    Move(
        "services/report_service.py",
        "report.py",
        [],
    ),
]

# 通用导入重写：对所有迁移文件生效
COMMON_REWRITES: list[tuple[str, str]] = [
    # 共享集群已提供的能力，一律改为依赖 ecom_shared，不再本地重复实现
    ("from ..errors import", "from ecom_shared.errors import"),
    ("from ..core.llm import", "from ecom_shared.gateway import"),
    ("from ..core.rag import", "from ecom_shared.rag import"),
    ("from ..core.rerank import", "from ecom_shared.rag import"),
    ("from ..core.vectorstore import", "from ecom_shared.rag import"),
    ("from ..core.embeddings import", "from ecom_shared.rag import"),
    # 包内相互引用
    ("from ..connectors.", "from .connectors."),
    ("from ..core.tabular import", "from .tabular import"),
    ("from ..core.validators import", "from .validators import"),
    ("from ..config import", "from .config import"),
    ("from .report_service import", "from .report import"),
    ("from ..services.", "from ."),
]

PYPROJECT = '''[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "ecom-data-platform"
version = "1.0.0"
description = "{desc}"
readme = "README.md"
requires-python = ">=3.10"
license = {{ text = "MIT" }}
authors = [{{ name = "Li Wenyu" }}]

dependencies = [
    # 共享集群：模型接入、检索、记忆、工具协议全部复用它，本包不重复实现
    "ecom-agent-shared",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[tool.setuptools]
packages = {packages}

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
'''


def rewrite(text: str, extra: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """按规则重写导入，返回 (新内容, 命中记录)。"""
    hits: list[str] = []
    for old, new in list(extra) + COMMON_REWRITES:
        if old in text:
            count = text.count(old)
            text = text.replace(old, new)
            hits.append(f"{old}  ->  {new}  (x{count})")
    return text, hits


def migrate(pkg: str, moves: list[Move], *, packages: list[str], desc: str) -> None:
    dst_root = os.path.join(REPO, "packages", pkg)
    pkg_dir = os.path.join(dst_root, pkg)

    print(f"\n{'=' * 70}\n  迁移 {pkg}\n{'=' * 70}")
    all_hits: list[str] = []
    copied = 0

    for mv in moves:
        src_path = os.path.join(SRC, mv.src)
        dst_path = os.path.join(pkg_dir, mv.dst)
        if not os.path.isfile(src_path):
            print(f"  [跳过] 源文件不存在: {mv.src}")
            continue
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        text = open(src_path, encoding="utf-8").read()
        text, hits = rewrite(text, mv.rewrites)
        with open(dst_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        copied += 1
        print(f"  [复制] {mv.src:44s} -> {mv.dst}")
        all_hits.extend(hits)

    # 包根目录的空 __init__（各子模块自己会再补导出）
    for init_rel in {os.path.dirname(m.dst) for m in moves if "/" in m.dst}:
        init_path = os.path.join(pkg_dir, init_rel, "__init__.py")
        if not os.path.exists(init_path):
            open(init_path, "w", encoding="utf-8").close()

    # pyproject
    with open(os.path.join(dst_root, "pyproject.toml"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(PYPROJECT.format(desc=desc, packages=packages))

    print(f"\n  --- 导入重写命中（共 {len(all_hits)} 处）---")
    for line in sorted(set(all_hits)):
        print(f"    {line}")
    print(f"  已复制 {copied} 个文件 -> {pkg_dir}")


if __name__ == "__main__":
    migrate(
        "data_platform",
        DATA_PLATFORM,
        packages=[
            "data_platform",
            "data_platform.connectors",
        ],
        desc="AI 数据中台：多源数据接入（Shopify / Amazon SP-API / 飞书多维表格）+ 表格管道与规则校验 + 每日指标流水线与预警 + 知识库运维",
    )
