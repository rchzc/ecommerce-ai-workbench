"""模型调用降级判定的测试。

之前的实现是「任何异常都重试一次」，于是 401 / 429 / 超时都会被当成
"厂商不支持 JSON 模式"，既多花一次注定失败的调用，又把真实故障伪装成降级。
这里把判定边界钉死。
"""
from __future__ import annotations

from app.core.llm import is_unsupported_json_mode


class _FakeError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        if status is not None:
            self.status_code = status


def test_unsupported_response_format_is_detected():
    exc = _FakeError("Unknown parameter: response_format", status=400)
    assert is_unsupported_json_mode(exc) is True


def test_unsupported_json_object_is_detected():
    exc = _FakeError("response_format json_object is not supported", status=400)
    assert is_unsupported_json_mode(exc) is True


def test_auth_failure_is_not_retried():
    """401 重试一次只会再失败一次，还把错误归因带偏。"""
    assert is_unsupported_json_mode(_FakeError("Incorrect API key", status=401)) is False


def test_rate_limit_is_not_retried():
    assert is_unsupported_json_mode(_FakeError("Rate limit reached", status=429)) is False


def test_server_error_is_not_retried():
    assert is_unsupported_json_mode(_FakeError("Internal server error", status=500)) is False


def test_timeout_without_status_is_not_retried():
    """网络超时没有 status_code，不能因为取不到状态码就放行重试。"""
    assert is_unsupported_json_mode(_FakeError("Request timed out")) is False


def test_unrelated_bad_request_is_not_retried():
    """同样是 400，但内容与参数无关（如内容审核拒绝），重试无意义。"""
    exc = _FakeError("Your input violated the content policy", status=400)
    assert is_unsupported_json_mode(exc) is False


def test_plain_exception_without_status_still_inspects_message():
    assert is_unsupported_json_mode(_FakeError("unsupported parameter")) is True
    assert is_unsupported_json_mode(_FakeError("connection reset")) is False


def test_invalid_status_code_does_not_crash():
    exc = _FakeError("boom", status="not-an-int")  # type: ignore[arg-type]
    assert is_unsupported_json_mode(exc) is False
