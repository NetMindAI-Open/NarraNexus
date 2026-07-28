"""
@file_name: model_client.py
@author: Bin.Liang
@date: 2026-07-27
@description: ModelClient 协议的默认实现——把 LitellmClient 的原始 chunk 流
翻译成类型化 ModelEvent 流, 并按 ProviderProfile 应用方言。

分工: LitellmClient 管「连接与透传」, 本类管「语义」——cache_plan 翻译、
thinking 回放策略、usage 词汇归一、tool_use_start/arg_delta/tool_use 的
事件切分(pi 事件族形状)。旁路先例: AnthropicDirectClient(litellm 透传
不达标时的直连实现, 同一 Protocol, Assembly 换实例即切换)。
"""

from typing import AsyncIterator

from xyz_agent_context.agent_framework.nexus_loop.contracts.model import (
    ModelEvent,
    ModelRequest,
    ProviderProfile,
)


class LiteLLMModelClient:
    """默认 ModelClient: 一切 provider 先走 litellm 统一协议。"""

    profile: ProviderProfile

    def __init__(self, profile: ProviderProfile, client: object) -> None:
        """client: llm/litellm_client.py 的 LitellmClient 实例(依赖注入,
        测试可替换为回放桩)。类型注解用 object 以维持 contracts 层对
        llm 包的零依赖——真实类型在装配层保证。"""
        ...

    async def stream_step(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """一次模型调用的完整事件流。

        翻译职责:
        1. request.cache_plan 按 profile.cache_style 注入(breakpoints ->
           messages 里加 cache_control; prefix_auto -> 仅保证顺序稳定);
        2. chunk.delta.content -> text_delta; reasoning_content ->
           thinking_delta;
        3. tool_calls 增量: 首个含 name 的 chunk 先发 tool_use_start
           (E3 时序安全: 名字先于参数, policy 可提前裁决), 参数分片按
           content_index 聚合, 完整后发 tool_use; profile.supports_arg_delta
           时同步发 arg_delta(P3 消费);
        4. 末 chunk 的 usage 按 profile.usage_keys 归一, 随 "done" 事件产出。
        """
        ...

    def _translate_chunk(self, raw: dict) -> list[ModelEvent]:
        """单个原始 chunk -> 零或多个 ModelEvent(纯函数, 独立可测)。"""
        ...


class AnthropicDirectClient:
    """旁路首例(声明座位, v1 不实现): Anthropic 原生 SDK 直连。

    仅当四处透传实测(待拍板 #2)证明 litellm 在 cache_control /
    thinking 签名回放 / input_json_delta 上不达标时启用; 同一 Protocol,
    切换 = Assembly 换实例, 上层零感知。
    """

    profile: ProviderProfile

    def __init__(self, profile: ProviderProfile) -> None:
        """直连实现自管 SDK 依赖, 不经 LitellmClient。"""
        ...

    async def stream_step(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """同 LiteLLMModelClient 语义, Anthropic messages API 原生实现。"""
        ...
