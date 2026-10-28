---
code_file: src/narranexus/platform/artifact/_artifact_impl/confinement.py
stub: false
last_verified: 2026-10-28
---

# artifact/_artifact_impl/confinement.py — 把 artifact 的 file_path 钉在属主的根下

## 为什么存在

`instance_artifacts.file_path` 是用户可影响的值：bundle 导入写进来、LLM 工具参数写进来、
老布局的行也在。2026-10-28 审计发现 raw 读（[[raw_access]]）只检查"在 base_working_path
之下"，而 base 是**全体租户的根**；编辑写（[[user_edit]]）和 office 资产上传
（[[artifacts]]）干脆不限定。一条 `../<victim>/<agent>/x` 的指针三处都当成合法。

## 口径

- `owner_roots(agent_id, user_id, base, team_id)`：该 agent 的 workspace（`workspace_candidate_relpaths`
  ——当前 nested 布局 + 仍有真实数据的两种 legacy flat 目录名），有 `team_id` 时再加该
  team 的共享目录。**不含用户整根**：同一用户的兄弟 agent 目录也不行（raw 路由只凭
  view token，不过 JWT，放进去等于把兄弟 agent 的 `.env`/skill 密钥交出去）。
- 根是**字面路径**（`normpath`），绝不 `realpath`：这些目录都在租户自己的挂载里，租户能把
  workspace 目录换成指向别人目录的链接；对根做 realpath 等于把链接目标当成"自己的根"。
  entry 则做 realpath，链接一旦出现就落到根外、fail closed；根本身是链接也直接拒绝。
- `confine_entry(...)`：用 `resolve_workspace_relative_file` 做布局兼容解析 → realpath →
  必须落在某个根内，否则 `ArtifactPathEscape`；调用方映射成自己的 404/410。
- 公共入口：`ArtifactService.resolve_owned_entry(artifact)`，路由层只走它。

- `owner_roots` 同时就是"容器根"集合：[[raw_access]] 的单文件模式直接复用它（entry 直接
  落在任一 workspace 形态或 team 目录根上时不服务兄弟文件）。只拿"第一个存在的 workspace"
  会漏掉 nested 与 legacy flat 同时存在时落在 legacy 根上的 entry（PR #421 复审 I4）。

## 上下游

上游（"artifact 路径一律走 `confine_entry`"在代码里为真的全部入口）：[[raw_access]]、
[[user_edit]]（两处，确权后的 realpath 交给 [[commit]]）、[[freshness]]（越界 → "missing"）、
[[url_artifact]] 的 `set_embed_mode`（越界 → `ArtifactNotFound`）、经
`ArtifactService.resolve_owned_entry` 的 backend `agents/artifacts.py` office-asset 与
common_tools 状态块的 office 锁探测。[[heal]] 用同一口径的 `resolve_inside_workspace`
（它扫的是 scope 根而不是一条已存指针）。
下游：[[workspace_paths]]。注册时的 `_resolve_entry`（[[registration]]）是写入侧的同类
规则，本文件是读/改侧的。
