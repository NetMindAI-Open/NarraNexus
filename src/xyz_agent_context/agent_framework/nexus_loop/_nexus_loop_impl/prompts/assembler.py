"""
@file_name: assembler.py
@author: Bin.Liang
@date: 2026-07-27
@description: 系统提示词装配器——纯函数 section 流水线 + 三档 PromptMode。

三条装配纪律(07-22 §5.14B):
1. 每 section 一个纯函数, 条件不满足返回空串, filter 后拼接(OpenClaw);
2. PromptMode 三档(full/minimal/none)派生 subagent 精简面, 不维护两份 prompt;
3. 字节稳定性测试进 CI: 同输入多次装配必须逐字节相等(cache 前缀的地基)。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class PromptMode(Enum):
    """提示词裁剪档位(OpenClaw PromptMode 形状)。"""

    FULL = "full"          # 主 agent 完整面
    MINIMAL = "minimal"    # subagent 精简面(砍身份/记忆/表达类指引)
    NONE = "none"          # 只留一行身份(评判者等特殊场景)


@dataclass(frozen=True)
class PromptInputs:
    """装配输入包——全部来自平台注入的数据, prompts 组不 import 平台代码。

    Attributes:
        identity: Agent 身份/场景文本(来自 Awareness, 铁律 #4: 场景归平台)
        module_instructions: 模块指令(RESIDENT 全文 + CARD 索引)
        tool_specs: 在场工具清单(工具指南 section 由此生成, 只教在场工具)
        skill_index: 技能索引(description 常驻, 正文按需)
        workspace_files: 用户可编辑注入层(白名单 + 大小上限)
        narrative_facts: 长期事实(C 层)
        volatile: 易变尾部数据(日期/当前 plan/触发上下文/runtime 行)
    """

    identity: str = ""
    module_instructions: str = ""
    tool_specs: tuple[Any, ...] = ()
    skill_index: str = ""
    workspace_files: tuple[tuple[str, str], ...] = ()
    narrative_facts: str = ""
    volatile: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AssembledPrompt:
    """装配产物——显式区分稳定前缀与动态尾部(C2 约束的结构化表达)。"""

    stable_prefix: str     # S+C 层: session 内字节稳定, 是 cache 前缀
    dynamic_tail: str      # V 层: 每轮可变, 永远追加在前缀之后


SectionFn = Callable[[PromptInputs, PromptMode], str]


class PromptAssembler:
    """section 注册 + 顺序装配的唯一入口。"""

    def __init__(self, sections: tuple[SectionFn, ...]) -> None:
        """sections 顺序即拼接顺序(固定顺序是字节稳定的一部分)。"""
        ...

    def assemble(self, inputs: PromptInputs, mode: PromptMode) -> AssembledPrompt:
        """执行全部 section 纯函数, 过滤空串, 产出稳定前缀 + 动态尾部。

        本方法必须是纯函数: 不读时钟、不读环境、不产生随机——同输入
        逐字节同输出(CI 锁定)。
        """
        ...
