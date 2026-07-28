"""
@file_name: arg_stream.py
@author: Bin.Liang
@date: 2026-07-27
@description: 流式参数抽取器——把工具调用参数的 partial JSON 增量, 实时抽取
出声明为 streamable 的字段, 投影成 ui 轨 tool_arg_delta 事件。

这是「agent 对用户的回复也是流式」的技术核心: 表达工具(平台注入名单,
如 chat_module 的回复工具)的 content 字段在模型逐字生成参数时就流向前端,
用户体验与直接文本流无异;
model 轨仍只记完整 tool_use——日志/回放/cache 语义零影响(07-22 §5.6)。

pi(pi-ai)实测出的三条消费纪律, 实现必须遵守:
1. 字段可能缺失或截断在词中间——防御性检查存在性, 字符串按已到达前缀消费;
2. 不同 content block 的事件不保证连续——一切按 content_index 对齐;
3. 中途夭折(流断/被拦)——已呈现部分标错误态, 与普通流式中断同语义。
Codex 反面基线: 它不做参数级增量(参数在 item done 才完整到达), 证明本
能力是差异化而非必需——实现可以晚, 接口(streamable_fields 声明位)day-1 在。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldDelta:
    """一次字段增量: 某工具调用的某声明字段新到达的文本片段。"""

    call_index: int      # content_index 对齐
    field_path: str      # 声明字段名(如 "content")
    text: str            # 新增片段(已保证是该字段字符串值的前缀延续)


class StreamingArgExtractor:
    """单个工具调用参数流的增量解析器(每个 tool_use_start 建一个实例)。"""

    def __init__(self, streamable_fields: tuple[str, ...]) -> None:
        """streamable_fields 为空时本实例是 no-op(v1 全部为空, 测试锁定)。"""
        ...

    def feed(self, call_index: int, raw_delta: str) -> list[FieldDelta]:
        """喂入一片参数增量(partial JSON 片段), 返回可安全呈现的字段增量。

        实现要点: 增量 JSON 状态机(不是反复全量 re-parse); 只对字符串
        字段做前缀流式; 转义序列跨片段时缓冲到完整再放行。
        """
        ...

    def finalize(self, complete_args: dict) -> list[FieldDelta]:
        """参数完整后校对: 补发解析器保守缓冲的尾部, 保证「流式呈现的
        累计文本 == 最终参数值」(一致性不变量, 测试锁定)。"""
        ...

    def abort(self, reason: str) -> None:
        """中途夭折(取消/deny/流断): 标记已呈现部分为错误态。"""
        ...
