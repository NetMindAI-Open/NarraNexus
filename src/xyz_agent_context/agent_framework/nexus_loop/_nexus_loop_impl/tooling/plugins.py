"""
@file_name: plugins.py
@author: Bin.Liang
@date: 2026-07-27
@description: 插件/扩展注册面——第三方以标准 API 给 agent 加能力的唯一入口
(pi ExtensionAPI + OpenClaw plugin manifest 的合成)。

定位: MCP 是「协议级」外接(远程 server), plugin 是「进程内」外接
(本地代码注册工具/hook/prompt section)。两者最终都收敛为 ToolChannel
生态成员, 经同一 dispatcher/policy/日志——插件不拥有任何后门。

安全边界: 插件由平台白名单分发(云端多租户不执行用户任意代码);
桌面端可放宽为本地目录加载(feature-gate 区分, 铁律 #7 双模式各自明确)。
"""

from typing import Awaitable, Callable

from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import ToolSpec


class PluginApi:
    """插件初始化时拿到的注册面(能力清单即 API 表面, pi ExtensionAPI 形状)。"""

    def register_tool(self, spec: ToolSpec, handler: Callable[..., Awaitable]) -> None:
        """注册一个 function tool(进 PluginChannel 的清单, 与内建工具
        同规格同待遇)。"""
        ...

    def register_hook(self, event: object, fn: Callable[..., Awaitable], *, failure: str) -> None:
        """注册生命周期 hook(转发进 HookRegistry, failure 姿态必填)。"""
        ...

    def register_prompt_section(self, section_fn: Callable) -> None:
        """注册附加 prompt section——只允许挂在动态尾部(V 层), 禁止碰
        稳定前缀(C2 约束对插件的硬边界, OpenClaw provider 插件同款限制)。"""
        ...


class PluginManifest:
    """插件元数据声明(name/version/permissions/entrypoint)。

    permissions 显式声明插件要求的能力(文件/网络/表达), 装配层据此
    生成该插件工具的 PolicyContext——插件工具受管程度与内建一致。
    """

    ...


class PluginChannel:
    """全部已加载插件的聚合 ToolChannel。"""

    def __init__(self, manifests: tuple[PluginManifest, ...]) -> None:
        """声明期只记录清单; 加载/初始化在 connect 阶段。"""
        ...

    async def load(self) -> None:
        """按 manifest 加载插件模块, 依次调用其 setup(PluginApi);
        单插件失败降级为缺席(记 error 事件), 不拖垮通道。"""
        ...

    def list_tools(self) -> list[ToolSpec]:
        """全部插件注册的工具(带插件名前缀命名空间, 防冲突)。"""
        ...

    async def call(self, name: str, args: dict, ctx) -> "object":
        """路由到插件 handler 执行(异常收敛为错误型 ToolResult)。"""
        ...

    async def refresh(self) -> bool:
        """插件热重载缝(pi 热重载先例)——v1 恒 False, 桌面端 P4 启用。"""
        ...
