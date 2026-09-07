"""表格（CSV）读写：批量任务的输入与回写出口。

对应业务场景：运营手里的数据永远是表格 —— 选品清单、竞品调研表、评论导出。
让 AI 能力"进表格、出表格"，比做一个只能手动填表单的页面有用得多，
这也是「AI 真正跑起来、用起来」的落地形态。

刻意只用标准库 csv，不引 pandas：
    这个模块的职责只是"扁平字典 ↔ 一行 CSV"，pandas 会带来几十 MB 依赖
    和一堆用不上的 API，属于典型的"为了三行功能拖进一个框架"。
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any

# Excel 打开 UTF-8 CSV 中文会乱码，加 BOM 让它自动识别编码。
# 少这一个字节，运营同事打开导出的文件看到的就是一堆问号。
BOM = "\ufeff"

# 单单元格最大字符数。超长内容（如 A+ 长文案）会被截断并打标记，
# 否则一个单元格塞进 3 万字，Excel 打开直接卡死。
MAX_CELL_LENGTH = 8000


def parse_csv(text: str) -> list[dict[str, str]]:
    """把 CSV 文本解析成字典列表（第一行为表头）。

    容忍空行与 BOM；表头去重，重复表头加序号后缀，避免后面的列覆盖前面的列
    （真实导出文件里"关键词"出现两次是常事）。
    """
    if not text or not text.strip():
        return []

    # 去掉 BOM，否则第一列表头会变成 "\ufeff商品名"，后续取字段全部取不到
    clean = text.lstrip(BOM)
    reader = csv.reader(io.StringIO(clean))
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if len(rows) < 2:
        # 只有表头没有数据行：返回空，让上层报"表格里没有数据"
        return []

    header = _dedupe_header([h.strip() for h in rows[0]])
    result: list[dict[str, str]] = []
    for row in rows[1:]:
        item: dict[str, str] = {}
        for idx, key in enumerate(header):
            item[key] = row[idx].strip() if idx < len(row) else ""
        # 整行全空（表格尾部常见）直接丢弃
        if any(v for v in item.values()):
            result.append(item)
    return result


def _dedupe_header(header: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for name in header:
        if not name:
            name = f"col_{len(out) + 1}"
        if name in seen:
            seen[name] += 1
            out.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 0
            out.append(name)
    return out


def flatten(data: Any, prefix: str = "") -> dict[str, str]:
    """把嵌套的模型输出拍平成一行表格数据。

    列表转成 JSON 字符串而不是拼接文本：列表元素本身可能是对象，
    拼成文本后再也拆不回去；JSON 至少能让下游（或 Excel 公式）再解析。
    """
    flat: dict[str, str] = {}

    if isinstance(data, dict):
        for key, value in data.items():
            flat.update(flatten(value, f"{prefix}{key}." if prefix else f"{key}."))
        return flat

    key = prefix.rstrip(".")
    if isinstance(data, list):
        flat[key] = _clip(json.dumps(data, ensure_ascii=False))
    elif isinstance(data, bool):
        flat[key] = "是" if data else "否"
    elif data is None:
        flat[key] = ""
    else:
        flat[key] = _clip(str(data))
    return flat


def _clip(text: str) -> str:
    if len(text) <= MAX_CELL_LENGTH:
        return text
    return text[: MAX_CELL_LENGTH - 3] + "..."


def to_csv(rows: list[dict[str, Any]]) -> str:
    """把字典列表导出为 CSV 文本（带 BOM，Excel 直接双击可开）。

    表头取所有行的键的并集并保持首次出现顺序：不同 Agent 输出的字段不同，
    甚至同一批里失败行和成功行的字段也不同，按并集取才能保证列不丢。
    """
    if not rows:
        return BOM

    columns: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in columns:
                columns.append(key)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    return BOM + buffer.getvalue()
