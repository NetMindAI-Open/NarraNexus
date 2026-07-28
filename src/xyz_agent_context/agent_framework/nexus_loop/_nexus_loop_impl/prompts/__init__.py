"""
@file_name: __init__.py
@author: Bin.Liang
@date: 2026-07-27
@description: prompts 组——全部系统提示词的集中存放地(唯一例外: 工具
description 跟 Tool 定义走, 单一事实源防漂移)。

对标四家的合成方案(07-22 §5.14B):
- OpenClaw: 每 section 一个纯函数 + filter 装配 + 字节稳定 CI 测试;
- Codex: per-model 模板目录(prompt 随模型走)+ 薄描述厚指南;
- Hermes: stable/context/volatile 按变动频率分层 + 行为处方条件注入;
- 叠加我们的 C2 约束: S 稳定层(cache 前缀)/ C 上下文层 / V 易变层
  (动态尾部)三段式, 时间只到日期。
文案模板 vendored 在 resources/(包相对路径解析, 仓库先例: marketplace)。
"""
