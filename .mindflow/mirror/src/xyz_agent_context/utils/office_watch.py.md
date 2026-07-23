---
code_file: src/xyz_agent_context/utils/office_watch.py
last_verified: 2026-07-21
stub: false
---

# office_watch.py — 实时 Office 预览的共享内核

## 为什么存在

承载"office 文档作为实时 artifact 预览"这个能力的**共享常量 + 纯逻辑**,让后端
office-watch 路由(`backend/routes/office_watch_proxy.py`)和 executor 的 watch
端点(`agent_runtime/executor_service.py`)引用同一份定义,避免漂移。

背景:office 文件(pptx/docx/xlsx)浏览器无法原生渲染,但 `officecli watch` 能起一个
自刷新的本地 HTTP 预览服务(SSE)。把它作为 artifact 的一种 kind
(`OFFICE_LIVE_KIND = application/vnd.officecli-live`),渲染成实时预览。

## 关键内容

- **`OFFICE_LIVE_KIND`**:office artifact 的 kind 字符串。单一真源——被
  artifact 注册实现(`xyz_agent_context/artifact/_artifact_impl/registration.py`,注册白名单 + 按扩展名自动纠正)和 office-watch 代理路由共同 import。
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
