---
code_file: src/narranexus/platform/browser/_browser_impl/policy.py
last_verified: 2026-10-08
stub: false
---

# 每个 origin 的任意脚本权限

普通 HTTP(S) 浏览不属于权限模型：导航、读取、截图、固定操作只校验 URL scheme 与控制权。
唯一被判定的能力是 `full_cdp_access`（`browser_run` 执行任意页面 JS）——它等于整个浏览器
（能以页面身份发请求、提交表单、跳转），所以默认拒绝，只能由 owner 在 Settings 里按 origin
显式配置。**没有聊天内的授权弹窗**：把它做成 yes/no 会训练用户对唯一一个没有上限的权限点同意。

## 2026-10-08（PR #410 review）— 只保留真正被判定的能力

此前模型还承载 downloads / uploads / auto_review 三个能力、turn/thread 有效期的会话授权、
denial、审批回执和 `allow_history_access`。它们唯一的消费者是网站审批子系统，而该子系统没有
任何调用方（review I1），于是整套删除（铁律 #2/#18），`decide()` 也不再接收 turn/thread
（scope 只是运行时自述值，不该作为授权边界，见 `_mcp_identity.THREAD_ID_HEADER`）。

旧文档无需迁移：`from_dict` 忽略未知键（旧 verdict 字段、grants、receipts、history 标志），
只含旧字段的 origin 不保留为空规则；已知键的非法 verdict（含旧的 `ask`）照样报错，绝不默认。

## 不变的匹配规则

origin 拒绝畸形端口、保留 IPv6 方括号、去掉默认端口；通配符要求 hostname 边界且不含裸域名，
精确匹配优先于通配符；未设值的 origin 条目继承默认策略。这些规则只门控显式工具，不是浏览器
网络沙箱：普通页面仍可自行加载资源和跳转。启动参数禁用了文件系统下载。
