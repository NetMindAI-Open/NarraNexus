---
code_file: src/xyz_agent_context/agent_framework/cli_helper_sdk.py
last_verified: 2026-07-09
stub: false
---

## 2026-07-09 — codex helper 沙箱收敛 + 小清理(PR review Important/Minor)

- **working_path=_HELPER_CWD**:`_run_codex_oneshot_inner` 给 `get_agent_loop_driver`
  显式传 `working_path=_HELPER_CWD`。两个作用:① 它是 executor seam
  (`RemoteAgentLoopDriver.__init__`)的**必填**位置参数,不传则 `AGENT_EXECUTOR_URL`
  一上线就 TypeError(潜伏);② 把 codex 的 `writable_roots`/`cwd` 从**后端进程 cwd**
  收敛到一次性 per-uid 临时目录——helper 输入是 narrative/实体等含用户与外部内容的文本,
  一次 prompt injection 就能让 codex 在应用目录里动文件,收敛后爆炸半径限于可弃临时目录。
- **_HELPER_CWD 加固**:路径加 `os.getuid()` 后缀、`os.makedirs(mode=0o700)`,避免共享宿主机
  上其他本地用户抢建/读该目录。
- 小清理:删空 `__init__`;`import json` 提到模块顶部。

`_run_codex_oneshot` 原来只 `driver.agent_loop(messages=...)`,而 codex driver 的
model / api_key / auth_ref 全读环境里的 `codex_config`——那是 **agent slot** 的配置,
不是 helper 的。后果:① claude agent + codex helper → `codex_config` 空 → 无凭据 →
unauthorized;② codex agent + codex helper → 跑 agent 的旗舰(gpt-5.5)而非 helper 的
`gpt-5.4-mini`;③ `record_cost(model=model_name)` 记的是 helper 解析模型、与实际不符。
修法:拆出 `_run_codex_oneshot_inner`,外层用 `cli_helper_config` 造一个
`CodexConfig(model=model_name, api_key/base_url/auth_type=…, auth_ref=CODEX_CLI_
CREDENTIALS_REF if oauth)`,`_codex_ctx.set()` 后跑、`finally` 里 `reset` 复位(不污染
agent 的 codex_config)。与 `_run_claude_oneshot` 自建 ClaudeConfig 对称,codex helper
从此自足(agent 非 codex 也能跑)。测试 `test_codex_oneshot_installs_helper_model_and_creds`
断言 driver 收到 helper 的 model + 规范 oauth ref、且事后 agent 配置复位。

## 2026-07-09 — claude helper 自己 stage OAuth 凭据(自足)

合并 #76 后,claude OAuth 走隔离 `CLAUDE_CONFIG_DIR`(`claude_oauth_config_path`),
凭据要先被 stage 进去。`_run_claude_oneshot` 现在在 `auth_type=="oauth"` 时,spawn 前
调 `xyz_claude_agent_sdk._stage_claude_oauth_credentials(env["CLAUDE_CONFIG_DIR"])`
(懒导入避环),使 helper **不依赖同轮 agent_loop 先 seed** 共享隔离目录——**agent 槽
是 codex、helper 是 claude 的混配**,或后台单独触发 helper(无前置 claude turn)时也能
认证。macOS 上该 stager 含 Keychain 导出(见 [[xyz_claude_agent_sdk]])。

## 2026-07-08 — codex 一次性路径修复(真机 E2E 暴露的三个 bug)

`claude_code` 路径真机 E2E 通过(haiku 一次性、带 schema 的结构化输出正确解析)。
`codex_cli` 路径此前**从未被真机结构化调用验证过**(单测只 mock 了 `_run_oneshot`,
绕开了 driver 事件循环),导致三个形状 bug 一起漏网:

1. **instructions.md 写空**:codex driver 的 `_build_system_prompt_and_user_msg`
   只从 `role=="system"` 的消息拼 instructions,并 pop 最后一条作 user turn。原
   `_run_codex_oneshot` 把 system prompt 拼进 user 内容、只传一条 `role:"user"`,
   于是 instructions.md 写空 → codex 进程启动即拒("model instructions file is
   empty")。**修法**:instructions 走独立 `role:"system"` 消息、user_input 单独
   `role:"user"`,与 `_run_claude_oneshot` 对齐。
2. **事件键读错**:codex_official 的 translator(`output_transfer`)吐的是
   `{"type":"raw_response_event","data":{...}}`——可见助手文本在
   `data.type=="response.text.delta"`、终态用量在 `data.type=="response.done"`。
   原代码读 `ev["raw_event"]` / `ev["usage"]`(此 translator 从不设这两个键)→
   永远累积不到文本 → 结构化调用在空 body 上 JSON 抽取失败。**修法**:改读
   `ev["data"]` 的对应形状。
3. **吞掉 `response.error`**:codex 把鉴权/配额失败作为终态 **error 事件**(不是
   异常)上报(如 `error_type="unauthorized"` + "access token could not be
   refreshed")。原代码忽略它 → helper 拿空文本 → 抛误导性的"could not extract
   JSON(空)",掩盖真因、且绕过 #68 凭据告警。**修法**:捕获 `response.error`,
   无文本时抛 `RuntimeError("codex CLI helper failed: {error_type}: {error_message}")`
   ——**同时带上 error_type**,否则 codex 的 auth 错误消息本身不含 `is_credential_error`
   的标记词(靠 "unauthorized" 命中)。

4. **默认模型无效**:`_DEFAULT_CODEX_HELPER_MODEL` 原为 `gpt-5.1-codex-mini`,但订阅
   走的是 **ChatGPT 账号**,它拒绝 API-key 专用的 `-codex-mini` 系列(400 "not
   supported when using Codex with a ChatGPT account",真机确认)。改为 `gpt-5.4-mini`
   (真机验证可用,也是 openai helper 的 onboard 默认)。真机探测:`gpt-5.5/5.4/5.4-mini`
   可用;`gpt-5.2-mini/gpt-5.1/gpt-5.1-codex-mini` 被拒。
5. **文本重复**:codex 把回复既按 `agentMessageDelta` 增量流式吐、又在 `item.completed`
   全量重吐一遍(措辞有时略不同),两者都被 translator flatten 成
   `response.text.delta` → 累积后是两份 JSON 拼接 → 抽取失败。**本次在抽取侧兜底**
   (见 [[openai_agents_sdk]] 的 `_first_balanced_json`,取第一个平衡对象),结构化调用
   因此稳定通过;根因(translator 双 emit,亦影响 agent 主链路)单独立项。

回归测试:`test_codex_oneshot_*`(在 `test_cli_helper.py`)mock driver 吐**真实
translator 事件形状**,分别钉住文本累积、system 消息分离、error 抛出+可分类;三者
在修复前全 FAIL、修复后全 PASS。抽取兜底见 `test_json_extraction.py`。

**真机 E2E 结论(2026-07-08)**:`claude_code`(haiku)与 `codex_cli`(gpt-5.4-mini)
两条路径的带 schema 结构化调用**均真机通过**。claude 路径确定性可靠;codex 是 agentic
CLI,**best-effort**——偶发输出裹 prose / JSON 不干净时会抛 `ValueError`(响亮失败,
非静默),属 codex 侧特性(铁律 #15),平台不干预。

# cli_helper_sdk.py — CLI-backed helper LLM（订阅同时覆盖 Helper）

## 为什么存在

当 helper_llm 槽位指向订阅（OAuth）provider —— Claude Code（`claude_oauth`）或
Codex（`codex_oauth`）—— 那份 OAuth 凭据**无法直连 Messages/Chat-Completions API**，
所以 helper 的小结构化调用改为**走同一个 CLI 一次性执行**。这就是"一次订阅登录同时覆盖
agent 主模型和 Helper LLM、无需二次配置"的实现（2026-07 P0）。

与 `OpenAIAgentsSDK` / `AnthropicHelperSDK` 接口一致（`llm_function` / `llm_stream`），
~15 个 helper 调用点通过 `get_helper_sdk()` 无感使用，绝不直接 import 本类。

两种后端，按 `cli_helper_config.framework` 选：
- **claude_code** → `claude_agent_sdk.query()` 一次性（`max_turns=1`、`allowed_tools=[]`、
  无 MCP、`cwd` 用中性临时目录），复用 agent loop 同款 `ClaudeConfig.to_cli_env` 凭据链
  （OAuth 时 key 留空，CLI 读 `~/.claude` 凭据）。`ResultMessage.usage` 给 token。
- **codex_cli** → 复用已注册的 codex agent-loop driver 一次性（复用其 CODEX_HOME/凭据
  staging 与解析），累积 `response.text.delta`，从终态事件读 usage。**best-effort**：codex
  是编码 agent 而非补全 API，结构化 JSON 靠 schema-in-prompt + 提取兜底。

结构化输出用与 AnthropicHelperSDK 相同的 prompt-engineered 路径（schema 塞进 system
prompt，客户端提取+校验 JSON），复用其 `_extract_json_from_llm_output` /
`_ParsedResult` / `_SimpleResult` / `record_cost`，下游看到的形状完全一致。

## 上下游

- 上游：`get_helper_sdk()`（helper_sdk.py）在 `_cli_helper_ctx` 被设置时 dispatch 到这里
  （优先级 cli > anthropic > openai）。
- `_cli_helper_ctx` 由 resolver 在 helper_llm 槽位是 OAuth provider 时装配
  （`build_cli_helper_config` → `RuntimeLLMConfigs.cli_helper` → `set_user_config`）。

## 陷阱

- OAuth 订阅调用可能上报 0 token（CLI 计费在订阅侧，不在我们）——有则记账，无则 warn 不报错。
- 成本上下文来自 `get_cost_context()`（agent_id, db），与其它 helper 一致。
