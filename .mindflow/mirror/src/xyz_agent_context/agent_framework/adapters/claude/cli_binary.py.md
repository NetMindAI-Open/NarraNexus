---
code_file: src/xyz_agent_context/agent_framework/adapters/claude/cli_binary.py
last_verified: 2026-07-29
stub: false
---

# cli_binary.py — 决定 agent loop 启动哪个 `claude` 二进制,并把决定说出来

## 2026-07-29 — 暴露 `effective_cli_version()`

缓存从 `(path, reason)` 扩为 `(path, version, reason)`,并把解析逻辑收进
`_resolve()`,`resolve_cli_path()` / `effective_cli_version()` 都只是它的取值器
(仍然每进程只解析一次、只打一条日志)。

为什么需要它:[[transcript]] 要把 CLI 版本写进每条记录的 `version` 字段,而**能
写的只有真正在跑的那个版本**。`PINNED_CLI_VERSION` 不行——解析器在版本不匹配时会
回落到 SDK 自带的二进制,那时 pin 描述的不是实际写入者。这是同一类错误的第二次
出现:第一次是我读 `claude --version`(PATH 上的诱饵)而不是请求体里的
`cc_version=`。

## 为什么存在

`claude-agent-sdk` 的 wheel 里**自带一个完整的 CLI 可执行文件**
(`claude_agent_sdk/_bundled/claude`,约 186 MB),而 `_find_cli()` 先查它、
命中就返回,**根本不看 PATH**。所以真正发出每一个请求的二进制是被 pip 依赖
锁定的,而不是 `npm install -g @anthropic-ai/claude-code` 装的那个。

这件事此前没有任何地方记录,而 PATH 上那份是个**诱饵**:终端里
`claude --version` 回答的是一个 agent loop 从不启动的二进制。2026-07-29 的
排查中,这个诱饵连续造成两次版本误判,最后靠请求体自带的 `cc_version=`
计费头才定案。

之所以要紧,是因为两个版本行为不同。在本仓真实 HTTP MCP server 上实测
(实验 E3 / E3b / E3c,8 个 server、握手完成顺序随机化):

- SDK 0.1.43 捆绑的 **2.1.56 完全不做归一化** —— 请求的 `tools` 数组就是各
  server `tools/list` 按握手完成顺序的拼接,所以**每一轮都换序**(4 轮 4 个
  不同 order hash),`--resume` 路径同样如此。由于 `tools` 在缓存前缀里排在
  `system` **之前**,一个块位移就作废它后面的全部内容——包括我方整个 system
  prompt。
- **2.1.220 把 `tools` 归一化成严格字母序**。同样的对抗性条件,4 轮只有一个
  order hash、一个 bytes hash,连续 resume 轮之间前缀逐字节相同。

## 关键设计决定

**为什么是换 CLI 而不是升 SDK。** `ClaudeAgentOptions(cli_path=...)` 会短路
`_find_cli()`(`subprocess_cli.py:46`)。E3b 实测了这个配对:SDK 0.1.43 通过
stdio stream-json 协议驱动 CLI 2.1.220 完全正常,**跨了 83 个版本没有不兼容**。
相比升 SDK 0.1.43→0.2.128(要适配 adapter、`resume=` 注入、
`_graceful_cli_shutdown`、`_log_sysprompt_sha` 一整批接口),这是便宜一个量级
的路径。

**为什么必须验版本而不能只看路径存在。** 唯一不会对自己撒谎的信息源是执行
那个二进制本身。`run.sh` 原来是**不 pin** 的 `npm install -g`,云端镜像 pin
的又是另一个版本,所以"PATH 上有 claude"完全不能推出"它是修好的版本"。

**为什么任何不确定都回落到捆绑的。** 没有外部二进制、版本不匹配、
`--version` 读不出或挂死、显式路径不存在——全部返回 `None`,让 SDK 用它自带
的,也就是今天的行为。CLI 选择问题**绝不能阻断 agent 运行**(铁律 #14)。
特别注意"显式路径不存在时忽略而不是照传":照传会变成每一轮的
`CLINotFoundError`,比回落糟糕得多。

**为什么每进程只解析一次。** 探测要 spawn 子进程,热循环里每轮付一次是真实
成本。决定缓存在锁后面、只打一条日志——这条日志就是"这个进程实际用的是哪个
二进制"的答案,读它,不要去跑 `claude --version`。

## pin 的多锚点约定

`PINNED_CLI_VERSION` 以字面量镜像进 [[run.sh]] 和 Dockerfile.manyfold
(两者在安装 npm 包时都没法 import Python)。
`tests/agent_framework/test_claude_cli_pin.py` 断言三者一致 —— 与 CLAUDE.md
里"5 个 release version anchor 必须同步移动"是同一个模式。

**bump 这个值是行为改动,不是杂务**:换版本前必须重跑 E3 和 E3c。2.1.220 是
两个实验实际跑过的版本。

## 尚未覆盖

- **DMG 未纳入。** 桌面包用 `pip install .`(不读 `uv.lock`),按 `~=0.1.43`
  现场解析到 0.1.81 → 捆绑 CLI 2.1.139;而 `scripts/desktop-bundle/package.json`
  另外 pin 了 npm 的 2.1.119。改它需要同步重生成 `package-lock.json`
  (`npm ci` 要求),风险不为零,故本次刻意不动 —— fail-open 保证 DMG 行为与
  今天完全一致,只是拿不到这项收益。这是**铁律 #7 的一个已知缺口**,待单独处理。
- **PATH 上的这个二进制同时服务桌面 OAuth 登录**
  (`tauri/src-tauri/src/commands/auth.rs` 的 `claude auth login/status`)。
  pin 会同时改动那条链路用的版本。两条链路共用一个二进制、但对凭据存储的理解
  是否完全一致,**未验证** —— 2026-07-09 那次 "login expired" 事故的形态正是
  "登录的和用凭据的不是同一个二进制",值得单独核对。
