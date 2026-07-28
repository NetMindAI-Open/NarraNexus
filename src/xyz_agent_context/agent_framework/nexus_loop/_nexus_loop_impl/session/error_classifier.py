"""
@file_name: error_classifier.py
@author: Bin.Liang
@date: 2026-07-27
@description: ErrorClassifier 与 RetryPolicy 的默认实现(A5 契约执行者)。

分类表 = loop/events.py 的 CLI_ERROR_TYPES 六类 + CONTEXT_OVERFLOW
(OpenClaw 调研收编: provider 专属 overflow 错误串表)+ 平台扩展词汇
(executor_infra / config_actionable, 与现有 failure.py 对齐)。
"""

from xyz_agent_context.agent_framework.nexus_loop.contracts.errors import LoopError


class DefaultErrorClassifier:
    """异常 -> LoopError 的规则表实现。"""

    def classify(self, exc: BaseException) -> LoopError:
        """按异常类型 + 错误串规则表归一分类。

        规则表是数据(异常类/状态码/正则 -> ErrorType, retryable 位),
        不写 if 链; overflow 串表按 provider 分组维护(request_too_large /
        context length exceeded / ... 几十条, OpenClaw 清单为底)。
        未命中 -> UNKNOWN(retryable=False, 保守)。
        """
        ...


class NoRetry:
    """v1 默认 RetryPolicy: 错误即整轮结束(现状行为, helper fallback 在
    loop 外兜底)。"""

    async def should_retry(self, error: LoopError, attempt: int) -> bool:
        """恒 False(测试锁定)。"""
        ...


class StepRetry:
    """P3 座位: 可重试类错误重试当前 step(历史都在 ledger, 重试只花一个
    step 的钱); helper fallback 由此从主路径淡出为最后兜底。"""

    def __init__(self, max_attempts_per_step: int) -> None:
        """注意: 这是单 step 的重试上限(错误处理), 不是回合轮次上限
        (铁律 #14 禁区)——两者语义完全不同, 不许混淆。"""
        ...

    async def should_retry(self, error: LoopError, attempt: int) -> bool:
        """error.retryable 且 attempt < 上限 -> True(退避实现期定)。"""
        ...
