---
code_file: src/narranexus/platform/utils/office_watch.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — 两处路径解析都走 `resolve_inside_workspace`

`resolve_watch_file` 与 `ensure_watch` 原来都对 workspace（或候选）做 `.resolve()` 后比较；
workspace 目录被换成指向别的租户的链接时，会在对方树里起 watch、写 `.officecli_watch_*`
log/meta sidecar，并把对方文档经预览代理送出去。现在两处都以字面 workspace
（`resolve_existing_workspace` 只 realpath base）调用 [[workspace_paths]] 的
`resolve_inside_workspace`。`resolve_watch_file` 保持原有的 ValueError + 可读文案；
`ensure_watch` 是 executor `/watch/ensure` 的直接入口，失败契约本来就是返回 None，所以这里
记 warning 返回 None，不 spawn、不占端口。测试：`test_office_watch.py::
test_resolve_watch_file_refuses_a_symlinked_workspace` / `test_ensure_watch_refuses_a_symlinked_workspace`。

## 2026-08-13 — 生命周期健壮化(idle 自杀恢复 / 重启双 watch / SSE 逐出)

**(review 轮加固 ×3)** adopt 防 wrong-content 靠**跨平台进程身份**,不再是 Linux 专属:① **单射**——绝不 adopt 已被 _assignments 里另一文件占用的端口;② **身份=cmdline**——`_proc_cmdline`(Linux /proc/<pid>/cmdline;其它平台绝对路径 `ps -p` 0.5s 超时)确认该 pid 确实是我们那个 `officecli … --port <port>` 进程(`_cmdline_is_our_watch` token 级匹配:含 `officecli` + 相邻 `--port <port>`),回收的 pid 号几乎不可能命中;**拿不到 cmdline → 拒绝 adopt**(退回响的双 spawn:前端 could not open + Retry,远好过静默串文档)。**argv 单源**:`WATCH_PORT_FLAG` 常量被 `_watch_argv`(spawn)与 `_cmdline_is_our_watch`(匹配)共享,`test_identity_recognizes_the_real_spawn_argv` 捕获真实 Popen argv 焊死两端——改 argv 形状会立刻在 CI 红。**`ps -ww`**(review 轮 4):BSD/macOS 的 `ps -o command=` 在管道(非 tty)下把命令列截断到 79 列,而身份 token `--port <port>` 恰在 argv 末尾——装在长路径/子目录里就被切掉 → desktop adopt 静默失效、DMG 重启后头号 bug 回归;`-ww` 输出完整 argv 消除截断,`test_proc_cmdline_*` 覆盖真实 /proc 与 ps 两条路径的尾 token。start-time 判据 review 轮 3 删除(cmdline 非 None 即 return,死路径,铁律 #2);旧 sidecar 里残留的 `start` 键**不再被读取**(reconcile 只取 file/pid/port),老文件仍能正常解析(两个 fixture 保留该键作向后兼容覆盖)。**为什么必须跨平台**:reconcile 只在重启后 `_assignments` 空时跑,单射兜底此刻也为空——两 guard 失效窗口重合,不能靠单射兜身份(review 轮 2 抓出的 desktop no-op 洞)。残留假设:身份证明「该 pid 是这样起的 watch」,非「在听该端口的就是它」——单射 guard 补这个缺口,两者都得留。③ **reap 只由 pid 死亡驱动**——「pid 活 + 端口未监听」是启动窗口不误删(reap 有意不接身份判据:adopt 端已用 cmdline 拒绝陈旧 meta,reap 再接会在 cmdline 瞬时读失败时误杀健康 watch)。冲突一律放弃 adopt(绝不 release-then-grab)。

**磁盘为真 + reconcile**:spawn 时写 `.officecli_watch_<port>.meta`(file+pid+port);`ensure_watch` 内存无记录时先 `_reconcile_from_disk` 扫盘——同文件已有存活 watch(`_pid_alive` + `_port_listening`)则 adopt 复用,死 watch 的 sidecar 顺手 unlink 释放端口。**根治**:backend/executor 重启后内存 map 清空,旧 detached watch 仍在听,旧逻辑会给同文件另起第二个 watch(officecli 同文件单 watch→起不来→前端 4 次重试全败 'could not open'),正是「删相邻 artifact→docx 首挂载打不开」的服务端真身。**慢启动杀孤儿**:6s 没起来时 `_terminate_group(pid)`(start_new_session→杀进程组)+ 删 sidecar 再释放端口,不再留孤儿占「已被视为空闲」的端口。

# office_watch.py — 实时 Office 预览的共享内核

## 为什么存在

承载"office 文档作为实时 artifact 预览"这个能力的**共享常量 + 纯逻辑**,让后端
office-watch 路由(`backend/routes/office_watch/proxy.py`)和 executor 的 watch
端点(`agent_runtime/executor_service.py`)引用同一份定义,避免漂移。

背景:office 文件(pptx/docx/xlsx)浏览器无法原生渲染,但 `officecli watch` 能起一个
自刷新的本地 HTTP 预览服务(SSE)。把它作为 artifact 的一种 kind
(`OFFICE_LIVE_KIND = application/vnd.officecli-live`),渲染成实时预览。

## 关键内容

- **`OFFICE_LIVE_KIND`**:office artifact 的 kind 字符串。单一真源——被
  artifact 注册实现(`narranexus/platform/artifact/_artifact_impl/registration.py`,注册白名单 + 按扩展名自动纠正)和 office-watch 代理路由共同 import。
- **`WATCH_PORT_MIN/MAX`(26315–26334,20 个)+ `is_watch_port`**:分配器发放的端口池,
  同时也是**安全 allowlist**——代理转发前校验,防止它变成打进容器内其它端口
  (executor :8020、sqlite :8100)的 SSRF。20 个槽足够单用户任何现实的并发预览数。
- **`_allocate_port(abs_file)` + `_assignments`(锁保护)**:注入式端口分配器,替换了原来的
  哈希。按**绝对路径**记 file→port(跨 agent 全局唯一)。三条不变式消掉"串台"bug:①同一文件
  永远拿它记录的那个端口(活着就复用,idle 停了就在同槽重起);②**新文件只会拿到"既没被预定、
  又当前没在监听"的端口**——所以绝不会落到正在服务别的文件的端口上;③耗尽自愈:发放前先回收
  "已预定但 watch 已死"的槽。锁是必须的,因为 `ensure_watch` 跑在线程池(`run_in_executor`)。
- **`resolve_watch_file(agent_id, user_id, file)`**:workspace confinement + 存在性 +
  office 扩展名校验,返回 workspace 相对路径。
- **`ensure_watch(agent_id, user_id, rel) -> int | None`**:核心——先 `_allocate_port` 拿到
  该文件**专属**端口,已在跑就直接返回;否则 **detached** spawn `officecli watch`
  (`start_new_session=True`),所以它**活过调用方**(这是修掉"agent 用 `&` 后台起的 watch 在
  bash 工具返回时被杀"那个 bug 的关键),轮询就绪后**返回端口**(调用方不再自己猜端口)。
  **只在与 workspace 同主机时有效**(本地/桌面:后端进程;云端:executor 容器内)——因为它要
  与 agent 的 officecli 编辑共享 resident 才能实时刷新。

## 上下游

- **被谁用**:`office_watch_proxy.office_watch_open`(本地直接调 ensure_watch;云端经
  executor `/watch/ensure`)、`executor_service.watch_ensure`(容器内调 ensure_watch)、
  artifact 注册实现(import OFFICE_LIVE_KIND)。
- **依赖谁**:`workspace_paths.resolve_existing_workspace`;`officecli` 二进制(PATH,由
  Docker/run.sh/build-desktop 预装);`subprocess`(detached spawn)。

## Gotcha

- `ensure_watch` 用 `_officecli_bin()` 修 PATH(把 `~/.local/bin` 补进去)——MCP/后端子进程
  的 PATH 可能被剥,和 lark_cli_client 同一个坑。
- **历史(2026-07-13)**:原来是 `watch_port_for_file` 哈希到 5 个端口,`ensure_watch` 只判
  "端口在监听就复用"——两个文件哈希撞同一端口时,第二个 tab 会**静默显示第一个文件的文档**
  (5 端口下 3 文档并发过半概率触发)。实测 officecli 本身能多文件并发 watch(各端口各服务各的),
  瓶颈只在我们这侧,故改为注入式分配器 + 端口由 `ensure_watch` 返回。
- `_assignments` 是**进程内**状态:后端/executor 重启后表清空,但存活的 watch 仍在监听 →
  新分配会跳过这些在听的端口(不会串台),最坏是给重启前已有 watch 的文件多起一个 watch(轻微
  浪费,旧的 idle 自停);不影响正确性。
- **spawn 超时释放端口槽**(`_release_port`):watch idle 死后端口可能还没完全释放,新 spawn 撞不上
  →超时。此时释放该文件的槽,下次 open 换一个空闲端口重启,而不是死磕同一个卡住的槽——这是修
  "第四轮 Could not open the live preview" 的重启健壮性关键。
- **resident 共享的硬约束**:officecli watch 只在**相同 (cwd, 原始路径字符串)** 的 officecli 编辑
  时才推 SSE(resident 按这个键共享)。所以 watch 必须与 agent 用**完全一致的相对路径**从 workspace
  cwd 跑——`ensure_watch` 固定 `cwd=workspace + rel_file`,SKILL 也要求 agent 用同一相对路径。不一致
  就各起各的 resident,watch 收不到编辑、SSE 静默不刷(前端有 mtime 兜底重载兜住正确性)。
