---
code_file: src/narranexus/platform/browser/_browser_impl/install.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

CLI `--download-host` / `--manifest-url` 帮助写明：这是**完全信任**的二进制来源（下载的浏览器会被执行，
唯一检查是 --version 探测，CfT 不发布逐文件摘要）。源码里的 "design §" 引用已移除。

# Install coordination

The browser is an optional runtime installed by the user. BrowserService keeps
its existing install, cancel, status and progress contract; callers in the
backend and MCP processes share the same installation directory.

A coordinator shields the actual attempt from an HTTP waiter's cancellation.
Only the attempt clears its cancellation flag and progress, so a disconnected
request cannot erase another caller's cancellation. Cancelling while idle is
a no-op and cannot poison a future installation. Listener failures are logged
without losing the download.

The real downloader exposes machine-wide state through an OS file lock and
atomic JSON snapshots. The kernel releases the lock on process exit, so a crash
cannot leave a stale PID lock. Never unlink the lock file: another process
could otherwise lock a different inode. Cancellation is tied to a unique
attempt token, preventing stale requests from cancelling a later attempt.
Snapshots are observable only while a process holds the lock; stale progress
after a crash is ignored. The lock is per installation directory and shared
by processes using the same OS user's browser home.

Injectable downloaders remain usable without filesystem state. The public
status and outcome structures do not change. No database migration or service
restart is involved.

## Manual recovery

The package's real `__main__` delegates to this module's CLI. `install`, `status`
and `cancel` use the same downloader/OS lock/root as the app, so a terminal
retry resumes safely and cancellation reaches another process. Status and idle
cancellation create no runtime directory. Ctrl-C lets the async downloader unwind
and release its lock; unfinished archives remain eligible for resume.

`manual_install_help` returns JSON-ready recovery commands using `sys.executable`
without resolving its symlink (resolving a venv Python would lose its packages).
Commands carry the effective absolute root and mirror configuration because a
user's terminal may not inherit the app's environment. POSIX shells use shlex
quoting; Windows uses PowerShell literal arguments with the call operator.
An absent archive mirror is passed explicitly as an empty flag value, preventing
an unrelated terminal environment override from changing the download source.
Neither command generation nor module import downloads Chromium or needs
Playwright, uv, an active backend, or a source checkout.

The CLI accepts explicit HTTPS manifest and archive-mirror options. Invalid
sources fail before creating installation state and return actionable JSON
diagnostics. The manifest endpoint and archive base are independent; no public
mirror is invented or silently selected. A real local HTTPS regression exercises
both overrides, extraction/probe/publication, idempotency and profile preservation.

2026-09-23：将当前 engine 的测试 wheel 装入实际 `.app` Python 的独立副本，
从 checkout 外用 `-I` 执行五项 CLI 检查全部通过，包括真实本机 HTTPS 镜像、
profile 保留、跨进程取消与终端 mirror 环境隔离。fixture 是测试可执行程序；
这项证据证明打包解释器可以执行 installer，不等于最终整包或 sidecar 启动。
未安装 Playwright，185 项依赖检查通过。记录见桌面构建验证 todo。

随后最终 app 的实际 Python 重跑同一组五项 CLI 检查并全部通过。其独立启动
backend 返回的 manual_install 也确实指向 app 内的解释器，status_command
在 checkout 外成功执行；证据保存在 `/private/tmp/narranexus-desktop-final/`。
