"""
@file_name: profiles.py
@author: Bin.Liang
@date: 2026-07-27
@description: ProviderProfile 数据表——新 provider 优先 = 加一条数据行。

首批行(07-26 §6.4 + B1: DeepSeek 与 Anthropic 直连是第一批验证对象):
- anthropic: cache_style="breakpoints", thinking_replay="keep_signed"
- deepseek:  cache_style="prefix_auto"(平权在结构上成立)
- openai / netmind / minimax / yunwu: 实测后填行
铁律 #15: 本表描述 provider 的技术方言, 不做「模型适不适合当 agent」的
价值判断——用户选什么模型都能拿到一条 profile(未知走 DEFAULT)。
"""

from xyz_agent_context.agent_framework.nexus_loop.contracts.model import ProviderProfile


def builtin_profiles() -> dict[str, ProviderProfile]:
    """返回内置 profile 数据表(name -> profile), 声明期为空实现。

    表内容是数据不是逻辑: 增改行不触碰任何类; 每行的字段值必须有
    实测依据(litellm 透传四项测试), 不许拍脑袋填。
    """
    ...


def resolve_profile(model: str, provider: str | None) -> ProviderProfile:
    """按 model/provider 匹配 profile; 未知 provider 返回保守默认行
    (cache_style="none", 全部能力位 False)——保证任何用户模型可跑,
    只是拿不到优化。"""
    ...
