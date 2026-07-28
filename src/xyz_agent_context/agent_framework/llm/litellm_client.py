"""
@file_name: litellm_client.py
@author: Bin.Liang
@date: 2026-07-27
@description: 全仓唯一的 litellm import 点——大一统 model 适配器的原子封装。

职责边界(铁律 #9 的落地):
- 本文件只做「一次流式 chat completion 调用」的原子操作: 参数透传、
  流建立、原始 chunk 产出、连接治理(超时/关闭);
- 不做: 事件语义翻译(nexus_loop/modeling/model_client.py 的事)、
  错误分类(session/error_classifier.py 的事)、模型选择(providers 的事);
- 上层(nexus_loop、未来其他消费方)一律经本类使用 litellm, 其他文件
  出现 `import litellm` 视为架构违规(grep 可查)。

协议选择: litellm 的 acompletion(stream=True) 统一走 OpenAI
chat.completions 形状——tools/tool_calls/usage/thinking(reasoning_content)
均按该词汇透传; per-provider 差异(cache_control 注入等)由调用方按
ProviderProfile 处理后放进 extra 参数, 本类不理解方言。
"""

from typing import Any, AsyncIterator


class LitellmClient:
    """litellm 原子调用的薄封装(无状态, 可全局单例)。"""

    def __init__(self, *, default_timeout_s: float = 600.0) -> None:
        """default_timeout_s: 单次流的默认读超时(Hermes 教训: 流式必须有
        stale-stream 检测, 90s 无 chunk 视为僵死, 由上层配置覆盖)。"""
        ...

    async def stream_chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """发起一次流式 chat completion, 逐个产出 litellm 原始 chunk(dict 形状)。

        - chunk 不做任何语义加工, 消费方自行解析 delta/tool_calls/usage;
        - 流中断/超时抛原始异常, 由消费方的 ErrorClassifier 分类;
        - extra 全量透传给 litellm.acompletion(cache_control、thinking
          参数等方言内容在此通道进入, 本类不校验)。
        """
        ...

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """非流式兜底(Hermes 先例: 流式连续失败时回退), 返回完整 response。"""
        ...

    async def aclose(self) -> None:
        """释放底层连接池资源(进程退出/executor 回收时调用)。"""
        ...
