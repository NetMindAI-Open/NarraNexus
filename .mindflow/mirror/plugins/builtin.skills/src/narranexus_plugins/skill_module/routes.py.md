---
code_file: plugins/builtin.skills/src/narranexus_plugins/skill_module/routes.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — 500 不再回显异常原文

十处 `HTTPException(500, detail=str(e))` 改为固定文案 "Skill operation failed."；异常细节
由各 handler 原有的那一行 `logger.exception` 记录（每个异常只记一次）——2026-08-11 P2-2 的脱敏在插件里回退了。用户可操作的 400
（ValueError 文案）保持。
## 2026-10-28 — `skill_name` 段校验

带 `skill_name` 的 8 个 handler 在确权之后立刻 `_require_skill_name`（`safe_id_segment`
失败 → 404，与未知 skill 同口径），再构造 `SkillModule`。

## 2026-10-28 — agent_id 先确权（与 files 路由同类的洞）

十个 handler 都从 query/form 取 `agent_id`，直接交给 `SkillModule` 拼
`{base}/{caller}/{agent_id}/skills`，没有确权：`?agent_id=../<victim>/<agent>` 能列、读、改 env、
删，甚至把 skill **装进别人的 agent**。事件日志里没发现有人打过这条路，但同类必须同批修。
现在每个 handler 第一行是 `require_workspace_agent_owner(request, agent_id)`（经 [[web]] seam
到 [[_ownership]]）：身份必填、段安全、不是自己的一律 404；通过后才构造 `SkillModule`。
`current_user_id` 不再单独调用——身份与确权绑在一起返回。

## 2026-09-09 — install 端点分开汇报成功与失败（GitHub #95，复审 C3）

`pipeline.install_from_github` 返回列表（一个仓可含多个 skill，被拒的是 `status="failed"` 项），zip 路径包成
单元素列表走同一段：成功项各一行 installed / already installed，失败项一行 `was NOT installed: <error>`，
行间 `\n`，安全告警计数求和。**只有零成功才 400**（把全部失败行作为 detail）；有成功就 200，响应 `skill`
字段（单 skill 形状）取第一个成功项——被拒的兄弟不能掩盖已落盘、已审计的 skill。前端只用 success + message
并刷新列表，形状不变。**这段裁决放在 try 之外**（复审 round-2 C1）：try 里的 `except Exception` 会把 try 体内
raise 的 HTTPException 改标成 500——第一版正是这样把零成功的 400 变成了 500。本文件其余 handler 都有
`except HTTPException: raise`，install 是唯一没有的，改为不在 try 内 raise，并补上同样的 `except HTTPException: raise` 双保险（round-3 M1）。路由级测试
`tests/skill_module/test_install_route_multi_skill.py`（零成功 400 含全部失败行 / 部分成功 200 / 仓级 ValueError
400 / 表单错误 400）。

## 2026-09-07 — 宿主依赖改走 `narranexus.sdk.web`（批 6c，G2-I1）

批 6b 把 router 搬进插件包时，`from backend.*` 没跟着走：本文件当时还在 import 宿主的
私有模块（`routes._ownership` / `routes._mcp_egress` / `routes.dashboard.routes` 的下划线
函数）或非契约的公开符号（`backend.auth` / `backend.auth_errors` / `backend.config`）。
现在全部改成 `narranexus.sdk.web` 的 seam（实现见 [[plugin_sdk_host]]，形状见
[[web]]）。这不是改 import 路径的洁癖——第三方照抄这个 router 时，
`assert_owned` / `resolve_current_user_id` 一个都拿不到，只剩「自己重写鉴权」
（2026-08-12 那批 IDOR 的来源）这一条路。`pyproject.toml` 的
`plugins never import the host (backend)` 契约把这条线钉死，豁免列表为空。

## 2026-08-13 (review 轮) — 「已配置」判定统一到单一 helper

删除上一版的 `_downgrade_undecryptable_env_status` enrich pass(它按 name 解析 meta、命中不了 .disabled/ → 凭据完好的禁用 skill 全变红)。GET/PUT `/{name}/env` 两处统一调 [[skill_module]].configured_env_var_names。`_enrich_platform_env_status` **只降级「仅靠平台假设成立」的那半边**:读 `SkillInfo.env_platform_assumed`(由 [[skill_module]].env_config_status 在 parse 时算好、随对象带下来,内容=「必填 ∧ 属 PLATFORM_RESOLVED_ENV ∧ 非自存」的 var 名),把其中 DB 查不到 provider 的降为 False;`affected` 也从「requires_env 含平台 var」收窄成「env_platform_assumed 非空」。**关键修正(review round 3)**:上一版直接看 `requires_env` 里的平台 var,会把用户在 Skill tab **手填**了 `NETMIND_API_KEY` 的 skill 也降级(自存值走 parse 的 `v in configured` 支路,但 enrich 拿不到那半信息)——`netmind-vision`/`netmind-transcribe` 两个内置 skill 的 SKILL.md 正是指引用户手填这条路,会产生反向假阴性。现在自存平台 var 在 parse 时被排除出 `env_platform_assumed`,enrich 绝不碰它。enrich 的 `skill_module` 形参**刻意不用**(不得回查 stored meta),`tests/backend/test_skills_env_enrich.py` 的 `_FakeModule` 钉死这一点。**详情端点 `GET /{skill_name}` 也复用同一个 enrich pass**(`_enrich_platform_env_status(skill_module, [skill], user_id)`),否则它会给出比列表更乐观的 `env_configured`——新增 `env_platform_assumed` 后详情曾是唯一还给「平台假设」乐观答案的 HTTP 出口(2026-08-13 round 4,🟢4)。

## 2026-08-13 — 解密失败 fail-closed(2026-08-01 事故)

新增 enrich pass `_downgrade_undecryptable_env_status`(list 端点,继 `_enrich_platform_env_status` 之后):`_parse_skill_md` 纯文件系统、把任何非空存值当「已配置」,导致坏凭据的 skill UI 显绿而运行时失败;此 pass 用 [[skill_module]].get_configured_env_var_names 把「必填 var 存值但解不开」的 skill env_configured 降为 False → UI 提示重录。fail-open,enrich 出错不 500 列表。

## 2026-08-11 — MCP egress SSRF 过滤（安全审计 P0-3）

构造 `mcp_servers` 后调 `backend.routes._mcp_egress.filter_public_mcp_servers`（cloud only）：丢弃解析到内网地址的 MCP，防运行时把内网响应带进模型上下文。local 模式透传（localhost MCP 合法）。

## 2026-07-21 — 平台共享凭证的真实配置状态

`_enrich_platform_env_status`:list/env 接口对声明了 PLATFORM_RESOLVED_ENV
的技能,按「显式配置 OR 用户有 NetMind provider 行」重算 env_configured
(GET /env 与 PUT /env 的返回同逻辑)。配了 System→NetMind 的用户看到
已配置(共享生效);没配的用户如实提示。


# routes/skills.py — Skill 安装、学习与环境配置路由

## 2026-07-20 — install/remove 端点接入 InstallPipeline(stage 3)

`POST /install` 与 `DELETE /{skill_name}` 改为经
`marketplace/_skill_marketplace_impl/install_pipeline.py`(7 步引擎:扫描 Gate → 依赖/
兼容校验 → 冲突+config 迁移 → 落盘 → meta hash 字段 → skill_installations
审计 → 自动归档)。响应模型不变;`backup_after_api_install` 从本文件移入
pipeline(所有安装入口统一享有)。行为差异仅两点:恶意包(HIGH 扫描规则)
现在被 400 拒绝;安装消息可能追加 "(N security warning(s))" 后缀。同版本
重复安装返回 success + "already installed" 消息(原先是静默覆盖重装)。

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 为什么存在

Skill 系统让 Agent 可以通过安装第三方工具包（从 GitHub 或 zip）来获得新能力。安装后 Agent 需要"学习"这个 Skill（通过 `AgentRuntime` 读取 `SKILL.md` 并执行配置步骤）。这个路由管理 Skill 的完整生命周期：安装、启用/禁用、删除、触发学习、查询学习状态、管理环境变量配置。

## 上下游关系

- **被谁用**：`backend/main.py` — `include_router(skills_router, prefix="/api/skills")`；前端 Skills 管理面板
- **依赖谁**：
  - `SkillModule` — 所有 Skill 文件系统操作（安装、列表、enable/disable、删除、env config 读写）
  - `AgentRuntime` — 学习任务里运行 Agent 来执行 SKILL.md 里的配置步骤
  - `MCPRepository` — 学习任务里加载 MCP URLs（与 websocket.py 相同的逻辑）
  - `AsyncOpenAI`（通过 `openai` 包）— `_extract_requirements_via_llm` 调用 gpt-4o-mini 解析 env var 需求

## 设计决策

**学习任务作为后台 asyncio 任务**

`study_skill` 接口立即返回 `{"study_status": "studying"}`，实际的学习过程（运行 `AgentRuntime`，可能需要几分钟）在 `asyncio.create_task` 里异步执行。前端通过 `GET /{skill_name}/study` 轮询状态。学习结果（成功/失败的摘要）通过 Agent 在 SKILL.md 指导下调用 MCP tool `skill_save_study_summary` 写回，或者在学习结束后由路由代码设置 fallback 状态。

**LLM 提取 env var 需求**

学习完成后，用 `gpt-4o-mini` 分析 SKILL.md，提取需要的环境变量（如 API keys）和二进制依赖（如 `node`、`gog`）。这比手动写 regex 更可靠，因为 SKILL.md 的格式是非结构化的自然语言。提取结果保存到 `.skill_meta.json` 的 `requires_env` 和 `requires_bins` 字段，前端用这些信息显示配置面板。

**`/{skill_name}/study` 路径优先级**

POST `/{skill_name}/study` 和 GET `/{skill_name}/study` 需要在 GET `/{skill_name}` 之前注册，否则路径匹配会有歧义。注释里说 FastAPI 用注册顺序匹配，更具体的路径优先——实际上同一路由器内 FastAPI 是按注册顺序（不是特异性）匹配的，所以这里 `/{skill_name}/study` 必须在 `/{skill_name}` 之前出现在代码里。

**zip 安装用临时目录**

zip 文件上传时先保存到 `tempfile.mkdtemp()` 创建的临时目录，解压安装后用 `finally` 块清理临时目录。这防止了磁盘空间泄露。

**内置技能拒删 → 400**

`remove_skill` 现在多接一个 `except ValueError`:`SkillModule.remove_skill` 对内置技能(`.skill_meta.json` `builtin: true`)会 raise `ValueError`(内置随 app 出厂、删了下次运行还会重新物化,只能 disable 不能 remove)。路由把它转成带可执行文案的 400,而不是漏进兜底的 500。前端据此提示用户"改用禁用"。

**所有 4xx 拒绝点统一过 `_reject()` helper**

`install_skill` 把每条返回 400 的路径都收敛到一个 nested 闭包 `_reject(reason)`，里面先 `logger.warning(...)` 带上 agent_id / user_id / source / reason，然后才 raise HTTPException。这是为了关闭一个真实事故里暴露的「日志黑洞」：之前 `raise HTTPException(400, detail=str(e))` 直接抛出去，detail 只塞进 HTTP 响应体，服务端日志里就只剩 access log 的 `status=400`，prod 排查时无法判断是哪条规则在拒。改用 helper 之后，每一次 400 都必有一条 WARNING 留底，不需要再让用户去翻 Network。透传给前端的 detail 文本不变，行为零变化。

## Gotcha / 边界情况

- **学习中状态防重入**：如果 Agent 正在学习某个 Skill（`study_status == "studying"`），再次调用学习接口会返回 `success=False, message="Already studying"`，不会启动第二个任务。但这只是内存里的检查——如果进程重启，`study_status` 可能在文件里被持久化为 "studying"（如果没来得及更新），下次无法重新触发。
- **`_extract_requirements_via_llm` 依赖 OpenAI**：如果没有配置 OpenAI API key（`openai_config.api_key` 为空），这个函数直接返回，不会提取 env var 需求。Skills 面板里的环境变量配置界面会是空的，用户看不到需要填什么。
- **Skill 学习时 Agent 写文件到工作区**：学习提示词里明确要求 Agent 把配置文件保存到 `skills/{skill_name}/` 路径（不是 `~/` 等），但这只是提示词约束，不是技术强制。如果 Agent 不遵守，文件可能保存到意外位置。

## 新人易踩的坑

`_get_skill_module` 创建 `SkillModule` 时传 `database_client=None`。`SkillModule` 的很多操作（文件系统操作）不需要 DB，但某些操作（比如创建 Job）会需要 DB。学习任务内部调用 `AgentRuntime` 时 AgentRuntime 自己会初始化 DB，不依赖 SkillModule 的 DB 参数。如果将来 SkillModule 需要直接读写 DB，要在这里传入有效的 `db_client`。

## 2026-08-18 — agent 可见提示不再点名单一 owner 工具

那句「你 MUST 用 `send_message_to_user_directly` 告诉用户」里的工具名已退役 —— 它拆成了
`reply_owner`（回答刚说话的 owner）与 `notify_owner`（未被问就主动告知），两者纪律相反。

改成「你桌上那个面向 owner 的工具」而不是列出两个名字，是刻意的：**本轮到底哪一个在桌上，由
平台决定并写在该轮开头那行里**（`render_origin_declaration`，见 [[message_source_handler.py]]），
而 `disallowed_tools` 会把另一个的 schema 从模型上下文里移走。这里再列一次名字，就等于让一段
散文去和平台每轮算出来的事实竞争 —— 而本次改造的起因正是那种竞争：prod 上两个明写「Do NOT
call」的工具被调用了 615 次。

同理，这里也不该改成「用 `reply_owner`」：skill 的执行语境不一定是 owner 聊天轮。

## 2026-09-04 · plugin-owned router (batch 3c.5)

`ROUTES` — `builtin.skills`' `/api/skills` router (mounted by `backend/plugins_host`, not by main.py); the `skill_module` helper imports are intra-plugin.

## 2026-09-07 — moved into the plugin package

This router is the plugin's own contribution (`narranexus_plugins.skill_module.routes:ROUTES`), not a file under `backend/routes/` that the plugin's manifest reached out to: the package is self-contained (a distribution that leaves the plugin out has no dead router in the wheel), and the backend no longer imports the plugin's private modules to serve it. Builtin routers keep their absolute prefixes (`/api/...`); third-party plugins live under `/api/x/<id>` — the one deliberate difference, recorded in docs/API_POLICY.md.
