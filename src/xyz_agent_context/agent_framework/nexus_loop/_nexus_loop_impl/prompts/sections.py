"""
@file_name: sections.py
@author: Bin.Liang
@date: 2026-07-27
@description: 全部 section 纯函数——S/C/V 三层的具体成员(07-22 §5.14B)。

每个函数: 输入 (PromptInputs, PromptMode) -> str; 条件不满足返回 "";
独立可测。模板文案从 resources/ 读取(包相对路径), 函数只做数据填充,
不内联大段文案——文案改动不碰代码。
"""

from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.prompts.assembler import (
    PromptInputs,
    PromptMode,
)

# ---- S 稳定层(cache 前缀) ----


def constitution_section(inputs: PromptInputs, mode: PromptMode) -> str:
    """S1 平台宪法: 独白/表达契约的 prompt 面。

    核心条款(resources/constitution.md): 你的文本输出是自我思考, 用户
    看不到; 触达用户/外部世界必须调用表达工具; 工具必须当轮执行;
    反幻觉收尾; steering 信任边界(只信 harness marker)。
    任何 mode 下都不为空——这是本框架身份的最低配置。
    """
    ...


def model_prescription_section(inputs: PromptInputs, mode: PromptMode) -> str:
    """S2 模型行为处方: 按模型族条件装配(Codex per-model 目录 +
    Hermes 行为处方)。弱模型的强制工具使用特化段在 resources/
    prescriptions/ 下按族一个文件。铁律 #15 边界: 处方只做通用引导,
    不针对特定用户模型注入限制。"""
    ...


def agent_identity_section(inputs: PromptInputs, mode: PromptMode) -> str:
    """S3 Agent 身份/场景(数据来自 Awareness, 平台注入)。
    MINIMAL/NONE 档裁剪。"""
    ...


def module_instructions_section(inputs: PromptInputs, mode: PromptMode) -> str:
    """S4 模块指令: RESIDENT 全文 + CARD 索引(发现权永不裁剪——
    agent 永远知道平台有什么, 取用才显式付费)。"""
    ...


def tool_guidelines_section(inputs: PromptInputs, mode: PromptMode) -> str:
    """S5 工具指南集中段: 薄描述厚指南(Codex 形状)。由在场 tool_specs
    生成——只教在场工具(Hermes 纪律), 与工具注册单一事实源联动,
    结构上杜绝「prompt 教了不存在的工具」。"""
    ...


def skill_index_section(inputs: PromptInputs, mode: PromptMode) -> str:
    """S6 技能索引: description 常驻、正文经 skill_view 按需加载
    (两级渐进披露, Hermes/OpenClaw 同款)。"""
    ...


# ---- C 上下文层(会话内较稳) ----


def workspace_files_section(inputs: PromptInputs, mode: PromptMode) -> str:
    """C1 workspace 注入文件: 用户可编辑层(OpenClaw SOUL/AGENTS/TOOLS/
    MEMORY.md 形状), 白名单 + 单文件/总量截断上限; 明确「不控制工具
    可用性, 只做软指引」。"""
    ...


def narrative_facts_section(inputs: PromptInputs, mode: PromptMode) -> str:
    """C2 Narrative 长期事实(平台注入的数据, 本组不管来源)。"""
    ...


# ---- V 易变层(动态尾部, 不进 cache 前缀) ----


def date_section(inputs: PromptInputs, mode: PromptMode) -> str:
    """V1 日期: 只到日期, 无时分秒(Hermes 神圣规则——时间戳每轮打穿
    cache 是现有系统 ~20K token/轮 的事故根源)。"""
    ...


def plan_section(inputs: PromptInputs, mode: PromptMode) -> str:
    """V2 当前 plan(P3 PlanLedger 上线后有内容, v1 恒空)。"""
    ...


def trigger_context_section(inputs: PromptInputs, mode: PromptMode) -> str:
    """V4 触发上下文: 本回合由谁/什么渠道触发(平台注入)。"""
    ...


def runtime_section(inputs: PromptInputs, mode: PromptMode) -> str:
    """V5 runtime 行: workspace 路径、执行环境等易变事实。"""
    ...


def default_section_order() -> tuple:
    """默认装配顺序(S1..S6, C1..C2, V1..V5)。顺序本身是契约:
    改顺序 = 打穿全部用户的 cache 前缀, 必须走显式评审。"""
    ...
