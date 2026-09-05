"""三级容错 JSON 解析的测试。

之所以重点测这块：模型输出是所有链路里最不可控的一环，
一旦解析退化，前端拿到的就是脏数据或直接崩溃。这里把每一级容错都钉住。
"""
from __future__ import annotations

import pytest

from app.core.llm import _extract_first_object, parse_json_lenient
from app.errors import ModelOutputError


def test_level1_plain_json():
    assert parse_json_lenient('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_level2_strips_markdown_fence():
    raw = '好的，结果如下：\n```json\n{"a": 1}\n```\n希望有帮助'
    assert parse_json_lenient(raw) == {"a": 1}


def test_level2_fence_without_language_tag():
    assert parse_json_lenient('```\n{"a": 2}\n```') == {"a": 2}


def test_level3_extracts_object_surrounded_by_prose():
    raw = '这是分析结果：{"verdict": "健康", "score": 88}。以上仅供参考。'
    assert parse_json_lenient(raw) == {"verdict": "健康", "score": 88}


def test_braces_inside_strings_do_not_break_extraction():
    """字符串里的花括号不能参与配对，否则会截出半截 JSON。"""
    raw = '前缀 {"tpl": "if (x) { return 1; }", "ok": true} 后缀'
    assert parse_json_lenient(raw) == {"tpl": "if (x) { return 1; }", "ok": True}


def test_empty_input_raises():
    for raw in ("", "   ", None):
        with pytest.raises(ModelOutputError):
            parse_json_lenient(raw)


def test_non_object_json_raises():
    """模型返回了一个数组 —— 这不是我们约定的结构，不能当成合法结果。"""
    with pytest.raises(ModelOutputError):
        parse_json_lenient("[1, 2, 3]")


def test_unparseable_text_raises():
    with pytest.raises(ModelOutputError):
        parse_json_lenient("抱歉，我无法回答这个问题")


def test_extract_first_object_returns_none_when_unbalanced():
    assert _extract_first_object('{"a": 1') is None


def test_extract_first_object_skips_to_next_candidate():
    """第一个 { 配不上对时，应继续往后找下一个，而不是直接放弃。"""
    text = 'a { b {"ok": 1}'
    assert _extract_first_object(text) == '{"ok": 1}'
