"""
@file_name: errors.py
@author: Bin.Liang
@date: 2026-07-27
@description: 错误分类契约(07-26 A5)——provider 原始异常归一为有限枚举,
驱动平台侧 fallback 跳过、熔断、前端徽章。

第七类 CONTEXT_OVERFLOW 是 OpenClaw 调研的收获: provider 专属 overflow
错误串(request_too_large / context length exceeded / ...)day-1 就单列一类,
将来被动压缩直接消费该分类, 不用回头改契约。
"""

from enum import Enum


class ErrorType(Enum):
    """错误类型枚举——与 loop/events.py 的 CLI_ERROR_TYPES 六类对齐并扩展。"""

    AUTHENTICATION_FAILED = "authentication_failed"
    BILLING_ERROR = "billing_error"
    RATE_LIMIT = "rate_limit"
    INVALID_REQUEST = "invalid_request"
    SERVER_ERROR = "server_error"
    CONTEXT_OVERFLOW = "context_overflow"   # 被动压缩(P3)的触发信号源
    UNKNOWN = "unknown"


class LoopError(Exception):
    """分类后的循环错误。

    Attributes:
        error_type: 归一后的枚举分类
        retryable: 是否可按 RetryPolicy 重试当前 step
        provider_raw: provider 原始异常/报文(诊断用, 不进用户可见文案)
    """

    def __init__(
        self,
        error_type: ErrorType,
        message: str,
        *,
        retryable: bool = False,
        provider_raw: object = None,
    ) -> None:
        """记录分类结果; message 是面向日志的一句话描述。"""
        ...
