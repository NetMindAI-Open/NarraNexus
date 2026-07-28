"""
@file_name: model.py
@author: Bin.Liang
@date: 2026-07-27
@description: 模型侧契约——「方言是数据, 不是代码」(07-26 §6.4)。

ProviderProfile 把 provider 差异描述成数据行: 新增 provider 优先 = 加一条
profile 数据; 仅当 litellm 透传失败才写旁路 ModelClient 类。DeepSeek 平权
在结构上成立: 它只是一条 cache_style="prefix_auto" 的 profile。
"""

from dataclasses import dataclass, field
from typing import Any, Literal

# v1 直接吃物化层拼好的 provider 消息(OpenAI/Anthropic 通用 dict 形状),
# P2+ 引入 RefsSource 后再类型化为独立结构。
ProviderMessage = dict[str, Any]


@dataclass(frozen=True)
class UsageVocabulary:
    """provider 返回 usage 字段的命名词汇表(双词汇问题的结构化版)。

    例: Anthropic 用 cache_read_input_tokens, OpenAI 用
    prompt_tokens_details.cached_tokens——翻译规则收敛在这里,
    ModelClient 实现按表取数, 不散落 if-provider 分支。
    """

    input_key: str = "input_tokens"
    output_key: str = "output_tokens"
    cache_read_key: str | None = None
    cache_creation_key: str | None = None


@dataclass(frozen=True)
class ProviderProfile:
    """provider 方言描述符——一行数据描述一家 provider 的行为差异。

    字段来源: 07-26 §6.4 + 四处 litellm 透传实测(待拍板 #2)的结论直接
    落进字段, 不写进代码分支。
    """

    name: str
    cache_style: Literal["breakpoints", "prefix_auto", "none"]
    thinking_replay: Literal["keep_signed", "strip", "as_text"]
    usage_keys: UsageVocabulary = field(default_factory=UsageVocabulary)
    supports_arg_delta: bool = False   # P3 流式参数投影的能力位(pi 规格)
    max_breakpoints: int = 4           # Anthropic 系 cache_control 断点上限


@dataclass(frozen=True)
class ModelParams:
    """一次回合的模型解析结果(model/provider/thinking 等), 由平台侧传入。

    loop 不做模型选择——provider_resolver 是平台的事, 这里只消费结果
    (与 Nexus 解耦: 换个平台传别的 params, loop 照跑)。
    """

    model: str
    provider: str | None = None
    thinking: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class McpServerSpec:
    """一个 MCP server 的接入描述: {url, headers?}(per-agent headers 在内)。"""

    url: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CachePlan:
    """PromptCachePolicy 产出的缓存计划(纯数据)。

    ModelClient 按 profile.cache_style 决定如何翻译成 provider 参数:
    breakpoints -> cache_control 注入; prefix_auto -> 只保证顺序稳定。
    """

    breakpoint_indices: tuple[int, ...] = ()
    prompt_cache_key: str | None = None


@dataclass(frozen=True)
class ModelRequest:
    """ModelClient.stream_step 的完整入参包。"""

    messages: list[ProviderMessage]
    tools: list[Any]                  # list[ToolSpec], 避免跨文件循环引用用 Any
    params: ModelParams
    cache_plan: CachePlan = field(default_factory=CachePlan)


@dataclass(frozen=True)
class ModelEvent:
    """ModelClient 流式产出的原子事件。

    kind 词汇(对齐 pi-ai 的事件族):
    - "text_delta" / "thinking_delta": 增量文本(ui 轨投影);
    - "tool_use_start": 工具名先到——policy 可在参数流出前按名裁决(E3 时序安全);
    - "arg_delta": 参数增量(partial JSON, 仅 supports_arg_delta 的 provider);
    - "tool_use": 完整工具调用(参数完整 JSON, model 轨记账);
    - "done": 本 step 结束, 携带真实 usage 与 stop_reason。
    """

    kind: str
    content_index: int = 0            # pi 纪律: 不同 content block 事件不保证连续, 必须按此对齐
    payload: dict[str, Any] = field(default_factory=dict)
