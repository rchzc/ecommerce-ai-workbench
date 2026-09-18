"""测试共用的配置工厂。

**为什么不是每个测试文件自己写一串 `Settings(...)` 关键字参数。**

`app.config.Settings` 现在继承共享包与数据中台的底座（`ecom_shared.config.Settings`
→ `data_platform.config.Settings`），字段会随那两层演进。手写字段清单的话，别人在
共享包加一个必填字段，四个测试文件会一起 `TypeError: missing N required positional
arguments` —— 报错指向测试文件、而不是被改的那个字段，看着像测试坏了，
实际是夹具跟不上。本项目真踩过：共享底座加字段后，这里大面积测试直接构造失败。

所以这里的做法是「先要一份合法的真实配置，再 replace 掉测试关心的字段」：
新增字段由 `load_settings()` 自动填好，夹具永远不用改。

基线的来源是 `app.config.load_settings()`，它读的是当前进程环境变量；而
`backend/conftest.py` 已经用 session 级 autouse 把环境钉成了「离线 + 目录隔离」，
所以这里拿到的是 mock provider、路径全指向系统临时目录的配置 —— 不联网、不脏仓。
"""
from __future__ import annotations

import dataclasses

from app.config import Settings, load_settings


def make_settings(**overrides) -> Settings:
    """造一份"能跑但不联网"的配置，`overrides` 里的字段覆盖基线值。

    默认值沿用历史测试的约定（dashscope + 假 Key + 假模型名），
    这样既有用例一行都不用改，又不会真的去调厂商。
    """
    base = load_settings()
    # 先铺默认值、再用 overrides 覆盖，最后一次性展开。
    # 不能写成 replace(base, model_light="light", **overrides) —— 一旦调用方也传了
    # model_light，Python 会在**拆包时**就抛 "got multiple values for keyword argument"，
    # 而不是让后者安静地覆盖前者。这个坑在 app/config.py 的 load_settings 里也踩过一次
    # （路径字典和基类字段都含 data_dir），本质是"用关键字参数拼配置"的通病。
    values = {
        "provider": "dashscope",
        "api_key": "test-key",
        "api_base": "http://example.invalid/v1",
        "model_light": "light",
        "model_heavy": "heavy",
        "model_embedding": "emb",
        "supports_embedding": True,
        "log_level": "CRITICAL",  # 测试输出里不要混日志
    }
    values.update(overrides)
    return dataclasses.replace(base, **values)
