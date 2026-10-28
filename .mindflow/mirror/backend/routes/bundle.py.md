---
code_file: backend/routes/bundle.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28（PR #419 review I-2）— 403 按异常类型判，不再嗅探文案

`/export` 改为 `isinstance(e, BundleForbidden)` → 403（类定义在 [[builder.py]]），删掉
`str(e).startswith("Forbidden")`。原因：改 builder 文案会让 403 静默退回 500，而第三方抛出的
恰以 "Forbidden" 开头的 ValueError 会被当成授权拒绝、丢掉 traceback。现在普通 ValueError
无论文案如何都走 500 + `logger.exception`（`test_export_does_not_sniff_forbidden_prose` 钉住）。
`ValueError("Agent not found…")` 仍是 500（不确认 agent 是否存在）。下面旧条目的"以 Forbidden
开头的 ValueError 映射 403"已被本条取代。

## 2026-10-28 — 导出时的归属拒绝是 403

`build_bundle` 对不属于导出者的 agent / team 抛 `ValueError("Forbidden: ...")`。`/export`
原来把所有异常都折成 500；现在以 `Forbidden` 开头的 ValueError 映射 403，detail 固定为
`Forbidden`（不回显对方的 id），其余异常仍是 500 + 固定文案。

## 2026-10-28 — from-url 上游失败不回显异常

`upstream fetch failed: {e}` 会把连接错误原文（含内网主机名/端口判断依据）带给用户；
改为固定文案，细节进日志。
## 2026-10-28 — from-url 云端只收 https + 默认端口

host allowlist 只钉住「哪台主机」；`http://` 或 `:22` / `:8443` 落在白名单主机上照样能借
这个端点探该主机的其他服务。云端现在在 allowlist 之后再要求 scheme 为 https、端口为空或
443（`_scheme_and_port_allowed`；非法端口如 `:99999` 也拒），否则 400，下载前就拦。
本地 / 桌面不加此限制：本地默认 allowlist 本来就为 `http://localhost:3001` 的 dev 网站
服务。判定用 `settings.is_cloud_mode`，和 allowlist 的模式判定同源。
## 2026-10-28 — preflight 落盘用固定文件名

`tmpdir / file.filename` 直接用了客户端的 multipart 文件名，Starlette 原样透传，
`../x` 或绝对路径就决定了写到哪（随后 `unlink`）。改为固定 `upload.nxbundle`，与
marketplace publish 同一规则。

## 2026-08-18 三审 — 闸口挪进线程

`validate_skill_archive_bytes` 现在走 `await asyncio.to_thread(...)`。

上一版只做到"不解压"就停了，但**解析本身也是 O(条目数)**：`ZipFile()` 构造
时就把整个中央目录扫完、给每条建一个 `ZipInfo`，`infolist()` 只是返回这个已经
建好的列表——也就是说 `MAX_SKILL_ARCHIVE_ENTRIES` 这道闸**在代价付完之后才
判**。实测：33 MB 上传、声明 40 万个空成员 ⇒ `ZipFile()` 构造 **655 ms** 纯
同步 CPU（`infolist()` 本身 0.00 ms）。50 MB 上限下约 1 秒，且可重复发。

比上一版那个 50 GB 解压小 1~2 个数量级，但**是同一个失败模式**：一个用户的
请求卡住所有人的帧。所以判据不是"这个检查便宜不便宜"，而是"它是不是同步 CPU
调用跑在 async 路由上"——是，就该进线程。

（原文里"这条 route 是 async 且没有 to_thread"那句作为不解压的理由，现在不成
立了，已改：不解压和挪进线程是**两件独立的事**，两件都要做。）

## 2026-08-18 — 归档上传验 zip：坏输入不再跨接口炸成 500

`upload_archive` 此前对 `source_type=zip` 只校验大小和 `skill_name`，**不看
字节到底是不是 zip**。坏包被原样落盘 + 登记（200），直到 `/export` 在
[[builder.py]] 的 `scan_zip_for_sensitive` 打开它才抛 `BadZipFile` → **500**，
错误文案 "Failed to build the export" 既不说哪个 skill 也不说哪份归档。

这是 2026-08-18 在 dev 环境验证 SEC-07 修复时实测撞到的（用假 zip 头的测试
文件上传成功，随后导出 500）。属**既有缺陷**，不是 SEC-07 引入；也是工单
#113「BadZipFile 误回 500」在另一条路径上的重现。

现在落盘前调 [[security.py]] 的 `validate_skill_archive_bytes()`，坏包
→ **400** 且文案告诉用户传什么。校验顺序是**先大小后 zip**：两者都会 fire
时，"太大了"是更可操作的那条消息。

⚠️ **这道校验只读中央目录，绝不解压**。初版写的是
`zipfile.ZipFile(io.BytesIO(contents)).testzip()`，那是个**比原 bug 更严重的
洞**：`testzip()` 会把每个成员完整解压一遍校验 CRC，deflate 压缩比可达
~1030:1，实测 199 KB 的包解压出 200 MB；`max_upload_bytes` 是 50 MB，也就是
~50 GB。而且 `upload_archive` 是 `async def`，`testzip()` 是同步 CPU 密集
调用、没有 `to_thread`，**整个事件循环被独占**——任何登录用户一次上传就能让
全站请求和 WS 帧排队、正在跑的 agent 流式输出停住。这正是铁律 #16 说的"我们
自己的 bug 成为打断源"。
另外 `testzip()` 遇到 CRC 错误**不抛异常**，它 `return zinfo.filename`；初版
把返回值丢掉了，所以那次最贵的调用实际什么都没挡住。

判据够用的理由：下游那个原本 500 的消费者 `scan_zip_for_sensitive` 也只调
`infolist()`。**不验 CRC** 是明写的取舍——中央目录完好、数据段损坏的包会被
放行，那种失败在导入侧按 skill 单独兜住。

> 又是「正确做法已存在但这条 route 没套用」：真正在收到 zip 时验字节的是
> `skill_module._extract_zip_safely`（500 条目 / 100 MB 上限），**不是**
> `backend/routes/skills.py`——后者只校验文件名后缀，全文没有一次 `zipfile`
> 调用。新的共享校验器就是照它那两个上限对齐的。
> 和 SEC-07 本身同一个模式，所以这次连测试的假数据一起改了——原来的用例用
> `b"PK\x03\x04payload"` 当 zip，正是这个洞让它能绿。

## 2026-08-17 — SEC-07：两个"客户端字符串决定文件路径"的洞

这条 route 文件里同时存在**写侧**和**读侧**两个同源漏洞，根因是同一句话：
*把请求里的字符串直接当路径用*。

**写侧 —— `/skills/archives/upload` 的 `skill_name`（已被 QA 实证）**

`skill_name` 是 multipart Form 自由字符串，原先直接拼进
`skill_archives/{user_id}/{skill_name}.zip`。`skill_name=../x` 跳出用户目录
（QA 用 `../qa-sec07-oneup-marker` 落到共用父层，dev 库残留 id=20），
`../{受害者user_id}/x` 就是**跨用户任意文件写**。同 codebase 的
`skills.py` 早就在用 `file_safety.sanitize_filename()`，这条 route 漏了——
属于"正确做法已存在但没套用"，和 SEC-01~06 的 IDOR 同模式、不同类型。

现在改走 [[skill_backup.py]] 的 `archive_target()`（唯一合法路径构造点），
且**先校验再做任何事**：拒绝时不建目录、不写字节、不留 DB 行。
`ValueError` 是用户可操作的输入校验 → **400**；让它冒成 500 就是把 #113
的 `BadZipFile` 误报 500 又犯一次。顺手补上 `enforce_max_bytes`
（`skills.py` 那条路径一直有，这条一直没有，整包进内存）。

"拒绝时不建目录"这句起初是**假的**：`archive_target` 内部会 `mkdir`，所以
只有非法 `skill_name` 那条真的零副作用，其余 3 条 400 和整个 github 分支都
会留下空的 `skill_archives/{user_id}/`——而测试参数表刚好只有非法名，绕开
了唯一出问题的分支。现已把构造点纯化（见 [[skill_backup.py]]），落盘时才
`ensure_archive_dir`，并给每条 400 补了"目录也不存在"的断言。

`contents` 的绑定与使用原先跨了分支（只在 zip 分支绑定，却在 github 分支
return 之后无条件使用），仅因为提前排除了第三种 `source_type` 才安全；已把
写盘 + upsert + return 整体收进 zip 分支内，将来加第三种 source_type 不会
变成 `NameError` → 500。

**读侧 —— `/export` 的 `skills[].archive_path`（顺带排查出来的，更重）**

`SkillExportSpec` 原先收 `archive_path` / `manual_zip_path`，前端把
`GET /skills/archives` 拿到的路径原样回传，[[builder.py]] 直接
`shutil.copy2(src_zip, 包内)` 再流回客户端 —— 任何登录用户传
`archive_path: "/etc/passwd"` 就能**读走后端进程能读的任意文件**。
两个字段已从 schema 删除（铁律 #2，不留兼容位），builder 改为自己按
`user_id` 查 `skill_archives`。客户端只决定 **install_method**，服务端
决定 **bytes** —— 和 builder 里"内置技能强制 builtin"那道 server-side
guard 同一立场。

`manual_zip_path` 前端从来没有任何地方赋值过（纯死字段），一并删除。

> 遗留数据不在代码修复范围内：dev 库 id=20 那行带 `../` 的
> `archive_path` 仍需人工清理（含实体文件
> `qa-sec07-oneup-marker.zip`）。在清掉之前，靠 [[builder.py]] 的
> 读侧 `is_within_user_archive_dir` 兜住。
>
> **口径修正（2026-08-17 二审）**：初版这里写的是"靠
> `is_within_archives_root` 兜住"，那是**错的**——id=20 存的字符串是
> `{root}/{qa_uid}/../qa-sec07-oneup-marker.zip`，`resolve()` 之后落在
> `{root}/qa-sec07-oneup-marker.zip`，**就在 root 里面**，root 锚点的判据
> 会放行它。真正兜住它的是 per-user 锚点。写文档时把"我打算实现的边界"
> 当成了"我实现了的边界"，这类话以后要用测试反证过再写。

## 2026-08-11 — 500 路径错误文案脱敏（安全审计 P2-2）

500 分支 `detail=str(e)` 收敛为固定文案；400/`ValueError`（scheme/allowlist/sha256 等用户可操作校验）保留清晰文案。

## 2026-07-13 — include_skill_secrets passthrough

`ExportRequest.include_skill_secrets` (default False) is forwarded to `ExportSelection`. The frontend's single 'full mode' checkbox sets it together with include_channel_credentials.

## 2026-07-13 — opt-in channel credentials in the export/import routes

`POST /export` passes through `include_channel_credentials`. `/import/preflight` now returns `credential_clashes` and `/import/confirm` returns `channel_credentials_imported` / `channel_credentials_skipped_conflict`. Thin passthroughs — the logic lives in `bundle/builder.py` + `bundle/importer.py`.

## 2026-05-18 — 新增 `/import/from-url`(Template 一键 install 的入口)

承接 templates marketplace feature(设计记录为作者本地,不入库)。
原 import 走"用户下载 → 浏览器上传 → /preflight"两跳;新 endpoint 让后端
自己 fetch URL → 接现有 `preflight()`,实现"网站点 install → app 自动拉到
review 页"的一键体验。

**核心实现**:
- 接受 `{url, expected_sha256?}`,JWT/X-User-Id 鉴权(跟 `/import/preflight` 同款)
- URL 必须 http/https,host 必须在 `BUNDLE_FETCH_ALLOWED_HOSTS` env 白名单里。
  默认值**按 mode 分**:cloud(`settings.is_cloud_mode == True`)= `narra.nexus,www.narra.nexus`;local(sqlite,DMG / `bash run.sh`)= 上面加 `localhost,127.0.0.1,[::1]`。env 显式设置永远 override mode 默认。
  这条 mode-aware 默认值是 2026-05-18 加的,起因:DMG 内嵌 backend 跑出去拉 `http://localhost:3001/...` 被默认 allowlist 拒,UI 显示 "Could not fetch the template / load failed"——local 模式装 marketplace bundle 是 first-class 场景,默认就要允许 loopback
- httpx async stream 下载到临时文件,enforce `MAX_BUNDLE_BYTES`(复用
  `bundle/security.py`)+ `_FETCH_TIMEOUT_SEC=30s` + 不 follow redirects
- 可选 sha256 校验(`file_sha256` 复用 security.py)
- 复用 `bundle.importer.preflight(bundle_path, user_id)` —— 不重复 preflight
  那一长串逻辑(zip 解析、name clash 检测、embedding compat 等),只是给它前
  置一个"取件代办"

**安全考量(每条挡一类攻击)**:
| 控制 | 挡的是 |
|---|---|
| URL host allowlist | SSRF(Capital One 类:把后端骗去访问 `169.254.169.254/...` 拿 IAM 凭证) |
| 拒 redirect | 上游 302 → 内网/metadata IP 绕过 allowlist |
| 云端只收 https + 443(2026-10-28) | 白名单主机上的其他端口/明文服务被借道探测 |
| size cap(MAX_BUNDLE_BYTES = 500 MB) | 50 GB 文件填满磁盘 |
| timeout 30s | hang server 占满连接池 |
| optional sha256 | 上游服务器被攻破后投放替换包 / URL 写错指向旧版本 |
| JWT/X-User-Id 鉴权 | 匿名调用刷流量 |

**`BUNDLE_FETCH_ALLOWED_HOSTS` env** 走 `os.environ.get` 直接读——目前
`settings.py::_DOTENV_PASSTHROUGH` 白名单还没加它(那块是 invite-code
branch 上的改动),所以本地 dev 要么把它 export 出去,要么等 invite-code
merge 后顺手加到 passthrough。生产 EC2 部署直接走 systemd/docker env,不
经 `.env`,无影响。

## 2026-05-13 — local 多用户隔离修复

`_user_id_for_request` 改走统一 helper
`backend.auth.resolve_current_user_id`——cloud / local 共享同一条
identity 路径。`.nxbundle` 导入导出现在按真实登录用户隔离，而不是
全部塌缩到 singleton "local-default"。

# bundle.py — REST routes for `.nxbundle` export / import

## 为什么存在

把 `bundle/builder.py` + `bundle/importer.py` + `bundle/skill_backup.py` 的能力暴露成 HTTP 端点供前端使用。Bundle 是用户级别的资源动作（不是 agent 级别），所以走 `/api/bundle/*` 命名空间，独立于 `/api/agents/`。

## 上下游关系

- **被谁用**：前端 `BundleExportPage.tsx` / `BundleImportPage.tsx`
- **依赖谁**：
  - `bundle.builder.build_bundle` / `bundle.importer.preflight` / `bundle.importer.confirm`
  - `repository.SkillArchiveRepository` — list / 手动上传归档
  - `backend.auth._user_id_for_request` — 拿 user_id（local 走 `get_local_user_id`，cloud 走 `request.state.user_id`）

## 设计决策

### 端点

| Method | Path | 用途 |
|---|---|---|
| POST | /export | 流式返回 `.nxbundle` zip |
| POST | /import/preflight | 上传 zip → 解析 + 检测冲突 → return token |
| POST | /import/confirm | 用 token 真正导入 |
| GET  | /skills/archives | 列当前 user 的归档清单 |
| POST | /skills/archives/upload | 手动补归档（github URL or zip 文件） |
| POST | /export/preview/bus-channels | 列出当前 closure 候选 message-bus 频道（前端 picker 用） |
| POST | /export/preview/artifacts | 列出每个 agent 的 artifacts（Artifacts tab 用） |
| POST | /export/preview/mcps | 列出每个 agent 的 MCP URLs（Skills & MCP tab 用） |

`bus_channel_selection`（List[str]）通过 `ExportRequest` 透传到 `ExportSelection`：None = 默认走 closure 自动过滤，传值则在自动过滤的基础上再做 allowlist 限制（仍要求 owner == user 且 ≥1 closure 成员）。

`mcp_selection`（Dict[agent_id, List[mcp_id]]，2026-05-15 新增）：默认 None / {} = **一个 MCP 都不导**（opt-in，跟其他默认 default-include 的字段不一样）。MCP URL 经常指向私网/私服，所以 1.1 起强制让用户挑。

`artifact_selection`（Dict[agent_id, List[artifact_id]]，2026-05-15 新增）：默认 None = 全收。注意 artifact 的实际文件永远跟 `workspace.tar.gz` 走，这里只是过滤 DB 指针行。

### Streaming response

Export 通过 `StreamingResponse` 把 zip 文件分片写回，避免 backend 把整个文件读到内存。`iterfile()` 在生成器关闭时自动清理 tempdir。

### Preflight 跨进程稳定

参见 `bundle/importer.py` 的 mirror md（B5 修复 + scaling_assumptions §1）。

> ⚠️ **SINGLE-WORKER ASSUMPTION 链路**：preflight 落到当前 process 的本机 fs；confirm 必须命中同一台机器（看到同一份 work_dir）。多 pod 时要么共享 volume 要么改对象存储。

## Gotcha

- `import_preflight` 的 `tmpdir` cleanup 在 `finally` 里——这只清上传的 zip 文件，不清 importer 创建的 work_dir（work_dir 在 `~/.nexusagent/bundle_preflight/`）。
- `import_confirm` 失败时 work_dir 不会立即清，靠 6h TTL cleanup 兜底。
- `upload_archive` 的 sha256 在 source_type=github 模式下填 `"pending"` —— 没真正下载 tarball。设计上 export 时再 lazy download，但 v1 没实现 lazy download，所以这种行不会被 bundle export 用到。修法：让 `upload_archive` 走 github 模式时立即调 `archive_github_tarball`。
