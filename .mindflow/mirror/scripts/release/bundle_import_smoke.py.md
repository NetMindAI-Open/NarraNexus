---
code_file: scripts/release/bundle_import_smoke.py
last_verified: 2026-09-23
stub: false
---

# scripts/release/bundle_import_smoke.py — 打包产物的导入冒烟

## 为什么存在

2026-09-10 之前，DMG 构建从头到尾没有一次 import 过自己打进去的代码：wheel 装完就签名、公证、发布，直到用户双击 .app 才发现每个 sidecar 都在 import 阶段就退出（mcp 2.x 改名 `streamablehttp_client`）。构建全绿，用户全红。这个脚本就是那道缺失的门：用**打包用的那个解释器**，把 `state.rs` 拉起的四个进程的**全部可导入入口**都 import 一遍——注意"进程数 ≠ 入口数"，`-m uvicorn backend.main:app` 这种形态有两个可导入的半边（runner 和 target），所以入口是 5 个不是 4 个。

另外单列了一组 `LAZY_RUNTIME_IMPORTS`（uvloop / httptools / websockets）：uvicorn 把协议与事件循环实现当成**导入路径字符串**存着，到 `Config.load()` 才 `import_from_string`，所以 `import uvicorn` 之后这三个根本不在 `sys.modules` 里。其中 `websockets` 最要命——它是本项目的直接依赖，但全仓 `src/ backend/ plugins/ packages/` 没有任何地方 import 它，唯一的消费方就是 uvicorn 的 `websockets_impl`；它在 bundle 里坏掉不会抛异常，只会让服务端对每个 upgrade 请求打一行 "Unsupported upgrade request"，桌面版聊天的 WS 全线不通，而构建是绿的。

## 上下游

- **调用方（两处）**：
  1. `scripts/release/build-desktop.sh` step 3.1，紧跟依赖安装之后（早于 Node/签名/公证，失败越早越省时间），跑在**构建机**上、源码 checkout 在场；
  2. `.github/workflows/build-desktop.yml` 的 "Verify the shipped app starts without the source tree"，在构建之后、两次上传之前，用**重定位后的最终 app** 跑同一份脚本，checkout 已被挪走——这是唯一"看得见用户所见"的调用点。
  两处的 `sys.prefix` 不同（构建机上是 `tauri/src-tauri/resources/python`，重定位后是 `$RUNNER_TEMP/relocated/…`），所以可迁移性判据**不能相对 `sys.prefix`**：同一个 `.pth` 在一处"落在解释器内"、在另一处"落在外面"，判据必须两处一致。
- **事实来源**：`ENTRYPOINTS` 是 `tauri/src-tauri/src/state.rs` 的 `bundled_services()` 的镜像；两者一致性由 `tests/release/test_desktop_build_uses_lock.py::test_smoke_entrypoints_match_state_rs` 守住。
- **`LAZY_RUNTIME_IMPORTS` 也有可执行守门**：`test_smoke_checks_uvicorns_lazily_resolved_deps` 断言 `websockets` 必须在名单里，并且**同一个循环**要同时迭代两个元组——拆成两个循环、把 lazy 那组降级成警告，是削弱这道门最自然的方式（"uvloop 挂了只是性能回退"很容易说服人），所以断言盯的是"共用同一条判死路径"，不是"两个名字各自出现过"。

## 先查"可迁移"，再查导入（v1.21.3 教训）

导入检查有一个它自己无法察觉的盲区：它跑在**构建机**上。v1.21.3 的 30 个 workspace 包全被装成 editable，每个都是一个指向 `/Users/runner/work/...` 的 `.pth`——在构建机上这些目录存在，所以 8 项导入全绿；在用户机器上不存在，于是 `No module named 'narranexus.contracts'`。所以 `main()` 第一件事是 `_non_relocatable_installs()`：任何 `direct_url.json` 标了 `editable`、任何 `_editable_impl_*` / `__editable__*` 钩子，以及 `.pth` 里**任何绝对路径**——包括今天恰好落在解释器目录之内的——都直接判死：app 搬到 `/Applications` 之后，构建机上"在解释器内"的那条绝对路径同样不存在。合法的 `.pth` 条目是相对 site-packages 的；按 step 3 同样方式装出来的真实 site-packages 里一个 `.pth` 都没有（2026-09-10 核过），所以这条不会误伤好构建。这类问题无法靠"导入成功"证伪，只能看安装本身。

## 设计决策

- **purelib 和 platlib 都扫**（去重后逐个）。今天在我们打包的 macOS python-build-standalone 上两者是同一个目录，但这是一个靠外部事实成立的收窄；换解释器发行版或出 Windows/Linux bundle 时两者会分叉，editable 安装落进没扫的那个目录就会被放行。问题标识用相对各自 site 目录的路径，避免两个目录里同名文件产生歧义。

- **任何异常都判死，不只是 ImportError**。第一版只判 ImportError，理由是"别让构建机的环境问题误伤"。这个取舍是错的：跨 major 的破坏在 import 期多数**不是** ImportError——starlette/fastapi 是签名变化的 TypeError、属性没了的 AttributeError，pydantic 是 PydanticUserError，builtin 插件加载失败是 loader 直接 raise。9/10 真正漂移进 DMG 的四个包里，只有 mcp 恰好以 ImportError 现身，四分之一的覆盖率。
- 能这么严，是因为**先把有状态的东西全指到临时目录**（HOME / `NARRANEXUS_PLUGIN_HOME` / `DATABASE_URL` / `NARRA_SURFACE`）。`backend.main` 在 import 期就建 FastAPI app 并加载 builtin 插件，会读 home、插件树和数据库；不隔离的话结果取决于谁在构建，那才是"环境误伤"的真正来源。隔离之后结果是确定的，于是不需要任何白名单。
- 曾考虑真启动 sqlite_proxy 探端口，更贴近现实但要拖进数据库与端口依赖；导入这一层已经覆盖了历史上真实发生过的故障形态，先取这个性价比。

## 平台前提

假定 POSIX bundle（我们只出 macOS dmg）。这一条只对 `uvloop` 有意义：它在 uv.lock 里是**有条件依赖**（非 win32/cygwin、非 PyPy），所以在 Windows bundle 上它本就该缺席，而这个脚本会因此判死。真要出那种 bundle，应该按平台**缩名单**，不是把异常吞掉——吞掉等于顺手把 httptools 和 websockets 的覆盖也一起丢了。

## 新人易踩

- 在 `state.rs` 加一个新服务却不加到 `ENTRYPOINTS`，测试会红——那不是测试烦人，是那个服务的依赖图确实没人验过。

## Optional browser packaging (2026-09-23)

The browser plugin stores its implementation in a contribution import string.
Manifest loading therefore does not prove BrowserModule can import. The same
fatal lazy-import loop now includes BrowserModule and runtime_launch, while the
five sidecar entrypoints remain aligned with Tauri's process list.

The smoke environment points NARRANEXUS_BROWSER_HOME at an absent scratch path.
Importing the desktop app must work without Chromium or the Playwright package;
browser installation remains an explicit user action outside the app bundle.
The wheel regression separately builds the actual plugin wheel, imports its
packaged manifest/contribution/module from a scratch directory with Playwright
imports blocked, and checks that absence produces NEEDS_HUMAN without creating
a runtime directory. This catches editable-source paths masking missing wheel
metadata, and accidental eager runtime dependencies.

Validation on 2026-09-23 used 33 actual workspace wheels installed into a
standalone interpreter with a clean site-packages directory and locked
third-party dependencies. From `/private/tmp`, isolated Python passed all
10 imports and the relocatability check; all 185 installed packages passed
dependency validation. BrowserModule also imported with both Chromium and
Playwright absent and did not create a runtime directory. The full unsigned
Tauri app subsequently built using command-scoped bundled LLD plus the installed
SDK 26.5; no global or repository toolchain settings changed. Its relocated
Python passed the same checks. An isolated native Tauri/WKWebView probe with the
production CSP observed `tauri://localhost`, HTTP 200 on both loopback hostnames,
and authenticated `hello/frame/pong` from the live browser socket, including a
nonblank decoded JPEG canvas. This is native transport and package-import
evidence, not a signed DMG or a full sidecar-startup test. Detailed commands and
logs are in `reference/self_notebook/todo/2026-09-21-desktop-window-offscreen-and-tauri-build-break.md`.

最终 2026-09-23 重打已包含当前 33 个 workspace wheels 和 Popper 的生产
dist（2241 项前端测试通过）。最终重定位 app 再次通过 10 项导入检查、185 项
依赖检查及五项 CLI 检查。其 Python 另在独立端口/空白 DB 真启动 proxy、API、
MCP，验证真实 browser_status 与 manual_install 命令后正常退出。源码/打包
内容哈希和准确命令见 `/private/tmp/narranexus-desktop-final/README.md`。
这项进程证据不等于启动固定端口的原生启动器；该项仍等待协调窗口。

最终稳定信号后再次重打，已包含 backend RunTasks / shutdown cleanup 修复。
`verify-final.log` 验证 1375 个 wheel 文件、996 个 Python 文件，backend SHA
匹配父线程给定值；最终 app 的 10 项导入、185 项依赖、五项 CLI、隔离
API/MCP 和真实 SIGTERM drain 回归全部通过。原生启动器未启动，既有三秒
强杀/无序 stop-all 不属于 Python drain 修复的保证范围。
