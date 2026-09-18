"""测试共用的数据与配置工厂。

抽出 `make_settings` 是因为「造一个能跑的 Settings」在新布局下有 20 多个字段，
四个测试文件各写一遍必然漂移 —— 改了共享包的字段、只更新了一处，
另外三处会在运行时抛 TypeError，而那个报错指向的是测试代码不是被改的字段。

`data_platform.config.Settings` 是 frozen dataclass，所以覆盖字段用
`dataclasses.replace` 而不是 `Settings(**{**s.__dict__, ...})`：
后者在 frozen 类型上要靠 `__dict__` 反射，一旦将来加了 `slots=True` 就静默失效。
"""
from __future__ import annotations

import dataclasses

from data_platform.config import Settings

CSV_HEAD = "date,shop,sku,orders,units,revenue,ad_spend,stock,safe_stock,reviews,bad_reviews\n"

BASE_ROWS = (
    CSV_HEAD
    # 正常基线日：三行数据
    + "2026-09-01,Amazon US,B0A,10,20,1000.0,100.0,500,150,10,1\n"
    + "2026-09-01,Amazon US,B0B,5,5,500.0,50.0,600,150,10,0\n"
    + "2026-09-01,Shopify,B0A,8,12,800.0,80.0,700,150,10,1\n"
    # 日报日：默认正常
    + "2026-09-02,Amazon US,B0A,12,24,1200.0,110.0,480,150,10,1\n"
    + "2026-09-02,Amazon US,B0B,6,6,600.0,60.0,580,150,10,0\n"
    + "2026-09-02,Shopify,B0A,9,13,900.0,85.0,680,150,10,1\n"
)


def make_settings(tmp_path, csv_text: str = BASE_ROWS, **overrides) -> Settings:
    """造一份"能跑但不联网"的配置。

    所有字段都指向 tmp_path，所以用例之间零污染；
    provider 固定 mock、vector_backend 固定 lexical —— 需要网络的测试会变成
    "本地能过、CI 挂掉"，最后没人跑。
    """
    sales_dir = tmp_path / "sales"
    sales_dir.mkdir(exist_ok=True)
    (sales_dir / "sales.csv").write_text(csv_text, encoding="utf-8")

    base = Settings(
        # --- 共享层字段：测试里不碰真模型 ---
        provider="mock",
        api_key="",
        api_base="",
        model_light="mock-light",
        model_heavy="mock-heavy",
        model_embedding="",
        supports_embedding=False,
        request_timeout=10,
        top_k=4,
        chunk_size=600,
        chunk_overlap=50,
        rerank_alpha=0.7,
        vector_dir=str(tmp_path / "vectors"),
        vector_backend="lexical",
        collection="test",
        session_max_turns=5,
        longterm_enabled=False,
        log_level="CRITICAL",  # 测试输出里不要混日志
        # --- 本包字段 ---
        data_dir=str(tmp_path),
        docs_dir=str(tmp_path / "docs"),
        import_dir=str(tmp_path / "import"),
        jobs_dir=str(tmp_path / "jobs"),
        reports_dir=str(tmp_path / "reports"),
        sales_data_dir=str(sales_dir),
        state_path=str(tmp_path / "pipeline_state.json"),
        batch_max_rows=10,
        batch_concurrency=2,
    )
    return dataclasses.replace(base, **overrides) if overrides else base
