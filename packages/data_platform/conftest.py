"""pytest 根配置：让测试无需安装即可 import 本包（含并联的共享包）。

具体策略见 `_bootstrap.py` 的模块注释 —— 那里是唯一实现，这里只负责调用。
留这一层是因为 pytest 会自动加载根目录的 conftest.py，是**唯一**能在
收集测试文件之前执行代码的入口。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _bootstrap import ensure_paths  # noqa: E402

ensure_paths(os.path.dirname(os.path.abspath(__file__)))
