---
code_file: scripts/release/build-desktop.sh
last_verified: 2026-09-11
stub: false
---

# scripts/release/build-desktop.sh — macOS DMG 构建

## 为什么存在

把仓库变成一个可分发的 .app：独立 Python 解释器 + 依赖、Node 运行时 + 两个 CLI、OfficeCLI、Lark skill 包、前端 dist、Tauri 壳，然后签名 / 公证 / 打 DMG。由 `.github/workflows/build-desktop.yml` 在 tag 上跑。

## 依赖安装必须走 uv.lock（2026-09-10 事故）

step 3 曾经是 `uv pip install "$PROJECT_ROOT"`——它按 pyproject 的**区间**重新解析，完全绕过 uv.lock，于是 DMG 装的是"构建当天 PyPI 长什么样"。那天 PyPI 上是 mcp 2.2.0（lock 写的是 1.24.0），`streamablehttp_client` 已改名，`mcp.client.websocket` 已删除，`utils/mcp_executor.py` 两个都 import，结果是发出去的包里每个服务都起不来。当天两种解析对比：176 个包里 99 个与 lock 不同（starlette 0.50→1.6、anthropic 0.72→1.4、fastmcp 2→4）——这个 DMG 跑的是一套从没有人测过的依赖图。

云侧从来没中招：`docker/Dockerfile.manyfold` 走 `uv sync --frozen`。这里是全仓唯一**按 pyproject 区间重新解析、且把结果交到用户手上**的安装路径。（`run.sh` / `deploy-cloud.sh` 的 `uv sync` 读 lock，只是可能顺手刷新它；`run.sh` 里 venv 重建的 fallback 分支那句 `uv pip install -e` 漏了 `--no-deps`，确实会重新解析，但只影响本地 dev venv，且紧跟一句 import 校验。）

现在的形状是 `uv export --locked` → `uv pip install -r`。**必须是 `--locked` 而不是 `--frozen`**：uv 里 `--frozen` 是"导出前不要更新 lock"，陈旧的 lock 会被**静默使用**；`--locked` 才是"断言 lock 不会变，会变就退非零"。所以有人加了依赖忘了 `uv lock`，构建会红在这一步，而不是打出一个缺包的 bundle——同一条用户可见的 ImportError，换了个触发条件。（`verify_release_artifacts.sh` 反过来故意用 `--frozen`：它只想解析 lock，不能因为本地 uv 与 CI 的 lock 格式差异而失败。）守门测试 `tests/release/test_desktop_build_uses_lock.py`。

## NetMind（Power）登录端点闸门（2026-09-09，B-40）

DMG 的 NetMind 端点在构建期烤进去：前端经 vite 读 `VITE_*`，启动器经 cargo `option_env!` 读后端变量，都来自跑本脚本的 shell。以前任何一个空着都会静默落回编译期的 protago-dev 默认值——丢一个 repo Variable 就能发出一个「用 GitHub 登录」打开 "Netmind AI Test"（accounts.protago-dev.com）的 DMG，构建还是绿的。现在脚本第一步跑 `check_desktop_netmind_env.sh env`（在任何下载/构建之前）：前后端 Power 开关必须一致；开着时 8 个端点全必填、https、`*.netmind.ai`、不能是 test/dev/staging 主机、前后端 auth 同主机；工作流在 tag 构建上设 `NARRANEXUS_REQUIRE_POWER_LOGIN=true`，Power 关着直接红。`npm run build` 之后再跑 `bundle frontend/dist`：产物里出现 `protago-dev` 即失败（runtimeConfig.ts 只在 vite dev server 里编进 dev 兜底）。守门测试 `tests/release/test_desktop_netmind_env_guard.py`。

## 其它已固化的决定（都各自付过学费）

- **非 editable——`--no-editable` 必须写在 `uv pip install` 上，不只是 `uv export` 上**：editable 会把构建机的绝对源码路径写进 site-packages，装到别人机器上全线 ModuleNotFoundError。v1.21.3 就栽在这：#390 把 flag 只挪到了 export 上，但 export 把 30 个 workspace 成员写成裸相对路径，`uv pip install -r` 照样把它们装成 editable（31 个 `_editable_impl_*.pth` 指向 `/Users/runner/work/...`），用户一打开就 `No module named 'narranexus.contracts'`。而构建和冒烟全绿——因为它们跑在构建机上，那些路径恰好存在。所以 step 3.1 现在先查安装本身是否可迁移（见 `bundle_import_smoke.py.md`），导入检查放在其后。
- **不带 `--extra plugins`**：桌面版是轻量版，claude-agent-sdk（~186 MB）由 Settings → Plugins 按需装；分类见 `tests/backend/test_plugins_extra_lockstep.py`。
- **CI 里的 uv 也钉版本**（`.github/workflows/build-desktop.yml` / `ci.yml` 的 `setup-uv` 带 `version:`）。`--locked` 断言的是"这个 uv 导出的 lock 一个字节都不变"，所以 uv 版本本身进了构建契约：不钉的话，哪天 setup-uv 装到一个会抬 lock 格式的新 uv，这一步会在**打完 tag 之后**红，是最贵的失败位置。
- **Python / Node 下载都校验 SHA-256**，且期望值写死在脚本里——不许把"下完再算"的哈希贴回来。
- **bundle 的 npm 依赖钉死在 `scripts/desktop-bundle/package-lock.json`**，用 `npm ci` 装。
- **step 3.1 导入冒烟**：见 `bundle_import_smoke.py.md`。

## 唯一"看得见用户所见"的检查在工作流里，不在这个脚本里

这个脚本里的所有门禁（包括 step 3.1 冒烟）都跑在构建机上，而构建机上源码 checkout 恰好存在——任何偷偷依赖它的 bundle 都能全绿通过，v1.21.3 就是这样发出去的。所以 `.github/workflows/build-desktop.yml` 在构建之后、两次上传之前加了一步 "Verify the shipped app starts without the source tree"：把最终签名好的 .app `ditto` 出来，把整个 `$GITHUB_WORKSPACE` 挪走（`trap` 保证恢复），然后用 app 自带的 Python 跑 app 里那份冒烟脚本，再按 state.rs 的顺序和启动方式把**四个 sidecar 全部真正拉起来**：启动前四个端口（8100 / 8000 / 7801 / 47831）必须全部空闲，否则直接失败并用 `lsof` 打出占用者——被别人占着的端口"通了"证明不了任何事；然后 sqlite_proxy 绑上 :8100 → backend 绑上 :8000 且 `/docs` 回 200 → mcp 绑上 :7801 → workers 绑上 :47831 且 `/healthz` 回 200、再存活 10 秒；每次端口一通都要 `kill -0` 复核是**自己的**进程还活着。最后四个进程都还在，而且每份日志（按 `$PIDS` 逐个，不另抄一份服务名清单）里既没有未捕获的异常行（`ModuleNotFoundError:` / `ImportError:` 开头），也没有**被捕获后记成一行日志**的导入失败文本（`cannot import name ` / `No module named `）。后一种才是端口和 `/healthz` 都看不见的那一类：supervisor 与插件钩子会 `except Exception` 后记一句 `... spec failed: {exc}` 然后照常启动，服务起来了、某个 channel/插件却悄悄缺席。只提到 `ImportError` 这个词的 warning 不会判死；DEBUG 级别的行也不参与判定——那是可选功能探测，不是失败（litellm 每次启动都会在 DEBUG 打一句 `Unable to import GenericAPILogger ... No module named 'litellm_enterprise'`，2026-09-10 本地端到端时撞到）。排除覆盖会进这些日志的三种 DEBUG 写法：litellm 的 `LiteLLM:DEBUG:`、loguru 的 `| DEBUG |`、stdlib `basicConfig` 默认的行首 `DEBUG:name:msg`；只认这三种形状，**不**排除任何含 "DEBUG" 字样的行（那等于放宽判死）。对未知日志格式这条规则是 fail-closed：顶多假红，不会放过真错。判定用一条 `awk` 而不是 `grep -v | grep -q`：在 `pipefail` 下，后者一旦提前命中就会让前一个 grep 收到 SIGPIPE，整条管道报失败，真命中反而读成"干净"。awk 的退出码三态分开处理：0 命中 → 判死；1 干净；其它（解析错误、读不了文件）→ 也判死并写明"这道门没跑"。否则它会是四道门里唯一"绿不代表跑过"的一道——而发版跑的是 macOS 的 BWK awk，本地测试覆盖不到。mcp / workers 各给 150 秒——state.rs 对这两个服务既不等端口也不查健康，CI 比产品严格是故意的。

环境也按 Finder 启动的样子收窄：PATH 只剩 app 自带的 `resources/nodejs/bin`、`resources/nodejs/node_modules/.bin`、插件目录 `~/.narranexus/plugins/nodejs/node_modules/.bin`（逐字对应 state.rs 的 `resolve_bundled_node_bins()`）加 launchd 的 `/usr/bin:/bin:/usr/sbin:/sbin`；部署模式相关变量全部 `unset`（否则 runner 上一个 `NARRANEXUS_DEPLOYMENT_MODE` 就能让 backend 判成云模式、因缺 JWT_SECRET 拒绝启动）。失败时 `fail()` 把出事那个 sidecar 的日志打进 step 输出，紧接着的 "Collect relocated-app sidecar logs (on failure)" 步骤在本 job 任一步失败时（`failure()` 是 job 级，不是"上一步"）把四份日志作为 artifact 上传（日志在 `$RUNNER_TEMP` 下，不上传就随 runner 消失；失败发生在验证之前时目录不存在，`if-no-files-found: ignore` 让它空转）；步骤结束只删体积大的 `.app` 副本。只查"能导入"不够——导入成功说明模块能加载，不说明服务能起来。守门测试从 state.rs 解析启动目标，state.rs 加了服务而这一步没拉起它，测试就红。不管依赖构建机文件系统的是 `.pth`、shebang、还是以后的什么新形态，都会在这里现形。本地 `build-desktop.sh` 不做这一步（没法在自己的 checkout 里把 checkout 藏起来）。

另：bundle 的 `python/bin/*` 里 49 个控制台脚本（`narranexus`、`uvicorn`、`mcp`…）的 shebang 仍写死构建机路径。当前无害——启动器只把 Node 目录加进 PATH，运行时代码也不按名字调用它们（2026-09-10 核过）。上面那步验证现在用的就是用户的 PATH 形态，所以运行时一旦按名字调到这些脚本，就会在那一步失败；它覆盖不到的只是四个服务启动期间没有走到的代码路径。

## 已知仍在漂移的面

- `npx skills add larksuite/cli`（Lark skill 包）没有版本钉子，拿的是构建当天的最新；失败有 WARN + 运行时 fallback 兜底，暂按可接受处理。
- `tauri/src-tauri/Cargo.lock` 在 `.gitignore` 里，Rust 依赖每次构建重新解析。
- `docker/Dockerfile.manyfold` 末尾那句 `uv pip install -e .` 没带 `--no-deps`，理论上也按区间解析；它紧跟 `uv sync --frozen`，lock 的图已就位且满足所有下界，所以实际不会动任何版本。记在这里是为了让"云侧不受影响"这个结论下次审计时不用重新怀疑一遍。
