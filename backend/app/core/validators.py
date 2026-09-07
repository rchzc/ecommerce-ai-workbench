"""输出规则校验：把「模型输出了什么」判定成「这条结果能不能用」。

为什么需要这一层（面试可讲）：
    单条调用时模型偶尔跑偏，人一眼就能看出来；批量跑 200 行时，
    一条脏输出混在表格里没人会发现，等它被复制进 Listing 才发现标题是空的。
    **批量化的前提是结果可判定** —— 所以必须有一层机器规则替人先过一遍。

对应业务 SOP 的「规则校验」环节：
    标准输入 → AI 处理 → 规则校验 → 人工审核 → 标准输出

设计原则：**校验只标记，不修复**。
    fail 的行留给人工处理，程序不去猜业务意图 ——
    自动补全一个"看起来合理"的标题，比留空标记为待处理危险得多。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PASS = "pass"
WARN = "warn"
FAIL = "fail"

# 模型在"不知道该填什么"时最爱返回的占位内容。
# 这些值看起来像正常输出，实际是空结果的伪装，必须单独识别出来。
PLACEHOLDERS = (
    "n/a",
    "na",
    "none",
    "null",
    "tbd",
    "todo",
    "待补充",
    "待定",
    "暂无",
    "无",
    "xxx",
    "...",
    "…",
    "-",
    "—",
)

# 顶层字段缺失达到这个比例，判定输出结构整体崩塌（不是个别字段问题）
COLLAPSE_RATIO = 0.5


@dataclass(frozen=True)
class Issue:
    """一条校验问题。level 只有 warn / fail 两种：warn 可用但需留意，fail 需人工介入。"""

    field: str
    rule: str
    level: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "rule": self.rule,
            "level": self.level,
            "message": self.message,
        }


@dataclass
class ValidationResult:
    status: str = PASS
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status != FAIL

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == WARN]

    @property
    def failures(self) -> list[Issue]:
        return [i for i in self.issues if i.level == FAIL]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "issues": [i.to_dict() for i in self.issues],
        }


def _expected_shape(node: Any) -> str:
    """从 output_schema 的描述节点推断期望类型。

    Agent 的 output_schema() 用的是"人类可读的描述结构"而不是标准 JSON Schema
    （例如 {"title": "string，产品标题"}、{"bullets": ["string，五点描述"]}），
    因为同一份结构还要写进 Prompt 给模型看。所以这里做**形状推断**即可：
    只看容器层级（对象 / 数组 / 标量），不校验标量内部。
    """
    if isinstance(node, dict):
        return "object"
    if isinstance(node, list):
        return "array"
    return "scalar"


def _actual_shape(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "scalar"


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, tuple, dict)) and len(value) == 0:
        return True
    return False


def _is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in PLACEHOLDERS


def validate_output(
    data: dict[str, Any],
    schema: dict[str, Any],
    *,
    required: list[str] | None = None,
    min_text_length: int = 2,
) -> ValidationResult:
    """按 output_schema 的形状校验一条模型输出。

    Args:
        data: Agent 输出的结构化结果（AgentResult.data）
        schema: 该 Agent 的 output_schema()
        required: 必填字段；默认取 schema 的全部顶层字段
        min_text_length: 字符串字段的最小有效长度，短于此视为 fail
    """
    result = ValidationResult()

    if not isinstance(data, dict) or not data:
        result.status = FAIL
        result.issues.append(
            Issue(field="*", rule="empty_output", level=FAIL, message="模型未返回任何结构化内容")
        )
        return result

    top_keys = [k for k in schema.keys() if isinstance(k, str)]
    required_fields = required if required is not None else top_keys

    missing: list[str] = []
    for key in required_fields:
        if key not in data or _is_empty(data[key]):
            # 空值和缺字段在业务上等价：都要人工补，所以合并成一条规则
            missing.append(key)
            result.issues.append(
                Issue(
                    field=key,
                    rule="missing_field",
                    level=WARN,
                    message=f"必填字段缺失或为空：{key}",
                )
            )
            continue

        value = data[key]

        # 形状校验：期望数组却给了字符串、期望对象却给了标量，
        # 下游按结构取值时一定会炸，提前在这里拦住
        if key in schema:
            want, got = _expected_shape(schema[key]), _actual_shape(value)
            if want != got:
                result.issues.append(
                    Issue(
                        field=key,
                        rule="type_mismatch",
                        level=WARN,
                        message=f"字段 {key} 类型不符：期望 {want}，实际 {got}",
                    )
                )

        # 占位内容：看似有值，实为空
        if _is_placeholder(value):
            result.issues.append(
                Issue(
                    field=key,
                    rule="placeholder",
                    level=WARN,
                    message=f"字段 {key} 是占位内容（{value!r}），模型未给出有效结果",
                )
            )
        elif isinstance(value, str) and len(value.strip()) < min_text_length:
            result.issues.append(
                Issue(
                    field=key,
                    rule="too_short",
                    level=FAIL,
                    message=f"字段 {key} 内容过短（{len(value.strip())} 字），疑似无效输出",
                )
            )
        elif isinstance(value, list):
            # 数组元素里混了占位内容时同样要提示，否则"5 条五点描述"里躺着一个 N/A 很难发现
            bad = [v for v in value if _is_placeholder(v)]
            if bad:
                result.issues.append(
                    Issue(
                        field=key,
                        rule="placeholder",
                        level=WARN,
                        message=f"字段 {key} 中有 {len(bad)} 项为占位内容",
                    )
                )

    # 结构整体崩塌：不是"某个字段没填好"，而是输出根本没按 schema 来，
    # 这种情况下逐字段 warn 会淹没重点，必须整体判 fail
    if required_fields and len(missing) / len(required_fields) >= COLLAPSE_RATIO:
        result.issues.append(
            Issue(
                field="*",
                rule="structure_collapsed",
                level=FAIL,
                message=f"输出结构不完整：{len(missing)}/{len(required_fields)} 个必填字段缺失",
            )
        )

    if result.failures:
        result.status = FAIL
    elif result.warnings:
        result.status = WARN
    else:
        result.status = PASS
    return result
