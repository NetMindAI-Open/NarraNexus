# 模板分享(Templates Marketplace)— PRD + 阶段落地

- **Trigger**:NarraNexus 已有 `.nxbundle` export/import,现在做"agent 模板分享"
  ——website 当 marketplace 入口,NarraNexus 做生产者 + 消费者
- **Branch**:
  - narranexus-website:`template_sharing_2026_05_18`
  - NarraNexus:暂无(Phase 2/3 才动后端)
- **Status**:Phase 1(website 列表 + 详情)已实现,待用户验收 + commit;
  Phase 2/3 设计已成稿,待拍板 + 开工
- **核心结论**:三阶段(website 发布 → 一键 install → 分享回流);Phase 1
  仅 curated + B1 存储(`public/templates/`),足够把链路跑通;关键架构 fork
  在第 4 节列了 4 个待定决策

---

## 1. 背景与目标

- `.nxbundle`(zip 含 manifest + agents + skills + narratives + events + bus
  channels + team)已经能用,导入流程是 `BundleImportPage.tsx` 的 3 步
  wizard(upload → preflight → confirm)
- 现在缺的是**发现 + 一键装 + 回流分享**——这就是这次的范围

**北极星问题** = 让任何用户能"看到一个 agent 模板 → 一键装到自己的
NarraNexus"。

## 2. 现有基础(不动)

- `backend/routes/bundle.py` — REST endpoint(`/api/bundle/export`、
  `/import/preflight`、`/import/confirm`),colocated 的 mirror md 在
  `.mindflow/mirror/backend/routes/bundle.py.md`
- `src/xyz_agent_context/bundle/` — builder / importer / id_field_map 等
- 前端 `frontend/src/pages/BundleImportPage.tsx` + `BundleExportPage.tsx`
- 颗粒度已经有:per-agent narrative/event/job/social/bus channel 选择,
  `accept_sensitive_zips` 安全开关,SensitiveZipDetected 异常类

## 3. 三阶段路线图

| Phase | 范围 | 改动面 | 状态 |
|---|---|---|---|
| **1. website 端 templates 区** | 列表 + 详情 + 下载 | website 单 repo | ✅ 初版完成 |
| **2. NarraNexus 一键 install** | 从 URL 直接拉 bundle + preflight | NarraNexus 后端 + 前端 + Tauri | 待开工 |
| **3. 从 app 分享回流** | 在 app 内做 share-safe export → 上传 website | NarraNexus 后端 + website | 待开工 |

## 4. 关键架构决策(4 个 fork — 待拍板)

### A. 谁能上传模板?

| 选项 | 优 | 劣 |
|---|---|---|
| **A1 仅 curated(推荐 Phase 1)** | 简单、没法务/abuse 问题 | 内容增长慢,靠团队推 |
| A2 任何用户上传 | 真正"生态"感 | 要认证 + moderation + UGC 法务 + sandbox |
| A3 提交→审核→发布 | A1→A2 的中间态 | 审核成本,但可控 |

**当前选择:A1**(已落地)。Phase 1 底部留了"通过 GitHub issue 提交"作为
社区贡献通道。

### B. Bundle 文件存哪?

| 选项 | 优 | 劣 |
|---|---|---|
| **B1 `public/templates/*.nxbundle`(推荐 Phase 1)** | 部署即生效,零基础设施 | git 仓库膨胀,clone 慢 |
| B2 对象存储(Vercel Blob / Cloudflare R2) | 标准做法,有 CDN | 多一个供应商要管,有少量成本 |
| B3 GitHub Releases | 免费、版本化 | 上传手动,API 限速,没 CDN |

**当前选择:B1**(已落地)。`Template.bundle_url` 是一个字符串字段,
phase 2+ 切对象存储**只换 URL,代码不改**。

### C. NarraNexus 端怎么"装"?

**最终目标:三种部署各做适配,底层共用 `POST /api/bundle/import/from-url`**

| 部署 | UX |
|---|---|
| Cloud (`agent.narra.nexus`) | website 按钮 → `https://agent.narra.nexus/app/templates/install?url=<bundle_url>` → 后端 fetch → 跑 preflight wizard |
| DMG (Tauri) | 注册 `narranexus://` URL scheme → website 按钮 `<a href="narranexus://install?url=...">` → OS 唤起 → Tauri 转给前端 |
| Local (`bash run.sh`) | 不优雅。两种 fallback:① "Open in local app" 按钮跳 `http://localhost:5173/app/templates/install?url=...` ② 退回"下载 + 手动 import" |

**当前选择:暂未实现**(Phase 2)。详情页 "Install in NarraNexus" 按钮已经
画好,标 "soon",等 Phase 2 接 endpoint。

### D. 分享回流走 app 内还是手动?

| 选项 | 何时 |
|---|---|
| **D1 Phase 3a:share-safe export + 用户手动上传(推荐起步)** | 现在,A1 配套 |
| D2 Phase 3b:app 内 share button → 自动 POST 到 website | A2 / A3 后,需要鉴权方案 |

**当前选择:暂定 D1**(Phase 3)。Share-safe export 还需要:`POST
/api/bundle/export-for-share`(strip credentials/env_config/user identity),
对应前端按钮 + 元数据表单。

## 5. Template Schema(Phase 1 — 待用户确认)

定义在 `narranexus-website/lib/templates.ts`:

```ts
export interface TemplateAgent {
  name: string;          // 展示名,从 manifest.agents_summary[].agent_name
  agent_id: string;      // 保留 internal id 用于将来匹配,UI 不展示
}

export interface TemplateManifestSummary {
  bundle_format_version: string;          // e.g. "1.1"
  narranexus_version_exported: string;    // e.g. "1.3.4" — 用作 min version
  agent_count: number;
  unique_skill_count: number;             // 同名 skill 跨 agent 算一次
  requires_external_mcp: boolean;         // manifest.mcp_hints_count > 0
  /**
   * 用户需要额外配的凭证(除 LLM provider 外)。
   * 例:["Lark (for daily delivery)", "Slack (for notifications)"]。
   * 空数组 = 一个 LLM key 就够。
   */
  requires_credentials: string[];
}

export interface Template {
  slug: string;                  // URL-safe id, /templates/[slug] 用
  name: string;
  short_description: string;     // 列表卡片一句话
  long_description: string;      // 详情页长描述,纯文本/轻 markdown
  categories: string[];          // 分类 facet(过滤用)
  tags: string[];                // 更细颗粒度标签
  bundle_url: string;            // Phase 1 是 /templates/xxx.nxbundle 相对路径
  bundle_size_bytes: number;
  bundle_sha256: string;         // sha256 of the .nxbundle FILE (not the
                                 // manifest.integrity_sha256 — that one hashes
                                 // pre-zip content and won't match file bytes).
                                 // 算法:`shasum -a 256 path/to/bundle.nxbundle`。
                                 // Phase 2 install 时 backend 算下载后的文件
                                 // sha256,跟这个比 → 防错装 / 防上游被攻破替换。
  author: { name: string; url?: string };
  license: string;               // "MIT" / "CC-BY-4.0" / etc.
  manifest_summary: TemplateManifestSummary;
  agents: TemplateAgent[];       // 详情页 agent 列表
  created_at: string;            // ISO date
  updated_at: string;
}
```

**为什么这些字段、为什么不要别的字段:**

- `bundle_sha256`:**保留在 data**,Phase 2 一键 install 时校验下载完整性。
  Phase 1 不展示——普通用户不会校验,徒增噪音。
- `agent_id`:**保留在 data**,future-proof(将来跨 template 引用、统计去重
  之类可能需要)。UI 不展示——用户看不懂。
- `preview_screenshots`:**暂不加**。Phase 1 一个模板,加截图槽位但不填会
  显得空。截图准备好再加字段。
- `downloads / rating / popular`:**暂不加**。Phase 1 没用户行为数据;
  Phase 2/3 起统计再加。
- `created_by` / `submitter_user_id`:**暂不加**。A1 curated 只有团队上,
  作者用 `author.name` 显示足够。A2 起再加。
- `min_narranexus_version`:**借用** `manifest_summary.narranexus_version_exported`。
  从 manifest 取的"导出时版本",通常是 "兼容此版本及之后"(NarraNexus 的
  bundle import 已经做了版本协商)。

**Phase 1 不在 schema 里的东西(以后加):**
- `bundle_signature`(发布方签名,Phase 2/3 信任模型用)
- `verified_publisher`(是否官方认证)
- `category_icon` / `cover_image`(视觉资产)
- `submission_id`(A2/A3 投稿溯源)
- `download_count` / `rating_avg`(社区行为)

## 6. Phase 1 落地情况(已实现)

### 文件清单

```
narranexus-website/
├── public/templates/
│   └── financial-briefing-team-20260515-0603.nxbundle     ← 第一个模板 (845KB)
├── lib/templates.ts                                        ← schema + 数据 + helpers
├── app/templates/
│   ├── page.tsx                                            ← 列表(client-side 搜索+过滤)
│   └── [slug]/page.tsx                                     ← 详情(SSG prerender)
└── components/header.tsx                                   ← 加 "Templates" 导航项
```

### UI 说明

**列表页**:eyebrow + 大标题 + 搜索框 + 分类 chip 过滤 + 卡片网格;每卡显示
分类徽章、agent 数、name、short_description、author、"View →"。

**详情页**:
- 主区:分类徽章 + name + author/license + 两个 CTA(Install — 灰着标 "soon" /
  Download — 工作)+ long_description + agent 列表 + 手动 import 步骤
- 侧栏:agents/skills/min version/bundle_format/size/license/updated 一栏总览
  + 需要的凭证(如 Lark)+ tags

**Curator 工作流**(写在 `lib/templates.ts` 顶部 docstring):
1. `cp my-template.nxbundle public/templates/`
2. 在 `lib/templates.ts` 加一个 entry,manifest 字段从 bundle 里读出来:
   `unzip -p the-bundle.nxbundle manifest.json | jq`
3. `git push` → Vercel auto-deploy

### 测试结论(回应用户疑问"本地 vs 域名部署有区别吗?")

**Phase 1 没区别**——纯静态内容,bundle URL 用相对路径(`/templates/foo.nxbundle`),
localhost:3001 和 narra.nexus 都正确解析到自己 host 下的 `/templates/...`。
没 cross-origin / cookie / auth / CORS。

只有 Phase 2 加"一键 install"按钮时会出现差异(install 目标 URL 取决于
NarraNexus 部署在哪)。

## 7. Phase 2 设计草图(待开工)

### NarraNexus 后端新增

`POST /api/bundle/import/from-url`(`backend/routes/bundle.py` 加 endpoint)
```
body: { url: str, expected_sha256: Optional[str] }
auth: 现有 JWT(cloud)/ X-User-Id(local)
flow:
  1. server-side fetch URL(超时 / 大小限制)
  2. 校验 sha256(若 caller 提供)
  3. 接现有 preflight 链路(写 work_dir,返回 preflight_token + manifest preview)
return: 同现有 /import/preflight 的 response
```

### NarraNexus 前端

- 现有 `BundleImportPage.tsx` 加 "from URL" 模式 OR
- 新 `BundleInstallFromUrlPage.tsx` 复用其 review/done 子组件
- 路由 `/app/templates/install?url=<bundle_url>&sha256=<...>` → 自动调上面
  endpoint → 跳 review 页

### Tauri 端(DMG)

- `tauri.conf.json` 注册 `narranexus://` URL scheme
- Rust side 接 URL → 通过 Tauri command 转给前端
- 前端走 install 页

### website 端按钮

`app/templates/[slug]/page.tsx` 的 "Install in NarraNexus" 按钮:
- 探测 user-agent / 给用户选择(Cloud / Desktop / Local)
- Cloud:`https://agent.narra.nexus/app/templates/install?url=<bundle_url>&sha256=<...>`
- Desktop:`narranexus://install?url=<bundle_url>&sha256=<...>`
- Local:fallback 给一个粘贴用的 URL,或退回 "下载 + 手动 import"

### 复杂度

涉及层数:NarraNexus 后端(新 endpoint)+ NarraNexus 前端(install 页)+
Tauri sidecar(URL scheme 注册)+ website 前端(按钮智能化)= **中高**
(三种部署都要联调)。

## 8. Phase 3 设计草图(待开工)

### Phase 3a — Share-safe export(用户手动上传)

NarraNexus 改动:
- `POST /api/bundle/export-for-share`(或现有 export 加 `share_mode: bool`)
- 自动 strip:credentials、env_config、user 标识、私人 social entities、
  API key 引用等
- 输出附带 README 模板让用户填:name / description / tags / license

前端:
- agent/team 详情页加 "Share as template" 按钮 → 元数据表单 → 触发
  export-for-share → 下载 bundle
- 提示用户"现在去 https://narra.nexus/templates/submit 上传"
  (Phase 3a 这个 submit 是 GitHub issue / 邮箱表单)

### Phase 3b — 自动上传(可选,A2/A3 时做)

- website 加 `POST /api/templates/submit`(DB + moderation queue)
- app 内点 share 直接上传
- 鉴权方案:OAuth-lite(narra.nexus 帐号 ↔ NarraNexus user link)或
  device-flow upload token

## 9. 跨阶段关键问题

### 信任 / 安全

bundle 含 skills,skills 含**可执行代码**。装别人的 template = 在你的
NarraNexus 跑别人的代码。

| 缓解 | 用在 |
|---|---|
| A1 curated 起步 | Phase 1 直接绕过 |
| install 前明示作者 + skill 数 + 警告 | Phase 2+ |
| 复用现有 `SensitiveZipDetected` 检测 | 立即可用 |
| bundle 签名 + 仅认证 publisher 走 full_copy | Phase 2/3 |
| 沙箱 | 长期 |

### 凭证清洗

现有 export 已经 strip 了 `api_keys / lark_oauth / user_password_hash /
user_providers`(见这次例子 bundle 的 `manifest.stripped`)——基础已经
做了。Phase 3a 在此基础上加一个 `share_mode` 把更激进的 strip 默认打开
(env_config 也清空、social entities 默认只留必要的等)。

### 版本兼容

- bundle 有 `bundle_format_version` + `narranexus_version_exported`
- import 端 preflight 已会比对(`embedding_compat.advice` 等已有)
- template 卡片侧栏展示 "Min NarraNexus version"

### Embedding 兼容

- bundle 用的 embedding model 可能跟 import 端不同
- 现有 preflight 已 surface advice
- 详情页可加"如果你用的不是 OpenAI text-embedding-3-small,需要 re-embed"
  之类提示——但 Phase 1 简洁起见暂不加

## 10. 复杂度评估(铁律 #17,结构性维度)

| Phase | 涉及 repo | 新文件 | 改文件 | 风险 |
|---|---|---|---|---|
| 1 | website | 3(lib + 2 pages) | 1(header) | 低 |
| 2 | NarraNexus 后端 + 前端 + Tauri + website | ~3 | ~4 | 中(三种部署联调) |
| 3a | NarraNexus 后端 + 前端 | ~1 | ~2 | 中 |
| 3b | NarraNexus 后端 + 前端 + website | 多 | 多 | 高(鉴权方案) |

## 11. Next step

- [ ] 用户验收 Phase 1 UI;若 OK → commit + push `template_sharing_2026_05_18`
- [ ] 拍板 Phase 2 是否现在做(还是等 invite-code 部署完一起)
- [ ] 拍板 Phase 3a 是否做、什么时候做
- [ ] 准备更多示例 template(目前只有 financial-morning-briefing 一个)
- [ ] 后期考虑:从 B1 迁 B2(对象存储)、加 preview screenshots、加 downloads 统计

## 取证记录(关键文件)

- 现有 bundle 系统:`backend/routes/bundle.py`、
  `src/xyz_agent_context/bundle/{builder,importer}.py`
- 现有前端 wizard:`frontend/src/pages/BundleImportPage.tsx`、
  `BundleExportPage.tsx`
- 例子 bundle:`/Users/ghydsg/Downloads/financial-briefing-team-20260515-0603.nxbundle`
  - manifest 内容见本文档 §5 schema 的字段映射
- Phase 1 落地:`narranexus-website` 分支 `template_sharing_2026_05_18`(未推)

---

## 更新 2026-05-18 — Stage A + Stage B 实现

承接前面 Phase 2 cloud-first 已 ship。Stage A(local terminal start)+
Stage B(DMG deep-link)在 2026-05-18 一起开工实现完成,等 build/install
循环验证。

### Stage A:website install chooser

`narranexus-website/app/templates/[slug]/InstallButton.tsx` 重写为
dropdown UI:单一 "Install in NarraNexus ▾" 按钮 → 4 选项菜单
(Cloud / Local Build / Desktop App / Download)+ 顶层 fallback 区域。

- 4 个选项每个有 name + 副标题
- 上次用过的选项打 "last used" badge(localStorage `nx-install-target`)
- 外部点击 / ESC 关闭 dropdown
- Cloud:`{NEXT_PUBLIC_NARRANEXUS_APP_URL || agent.narra.nexus}/app/templates/install?url=&sha256=`
- Local Build:`http://localhost:5173/app/templates/install?url=&sha256=`
- Desktop App:`narranexus://install?url=&sha256=` + fallback 探测
- Download:`<a download>` 静态文件
- 底部 footnote 两段说明 Local 和 Desktop 各自的前置条件

### Stage B:Tauri deep-link 接入(整套)

| 文件 | 改动 |
|---|---|
| `tauri/src-tauri/Cargo.toml` | `tauri-plugin-deep-link = "2"`;`tauri-plugin-single-instance` 加 `deep-link` feature(让二次启动的 URL forward 到现有实例) |
| `tauri/src-tauri/tauri.conf.json` | `plugins.deep-link.desktop.schemes: ["narranexus"]` —— bundler 据此往 `Info.plist` 写 `CFBundleURLTypes`,DMG 装到 /Applications 时 macOS Launch Services 注册 scheme |
| `tauri/src-tauri/capabilities/default.json` | 新增 `"deep-link:default"`(Tauri v2 capability 系统强制要 explicit grant) |
| `tauri/src-tauri/src/lib.rs` | 注册 plugin(顺序:single-instance → shell/updater → deep-link),`.setup()` 加 `on_open_url` callback:**(a) emit `deep-link-received` Tauri event**(给 hot 场景已 mount 的前端 listener)**(b) 写 `AppState::pending_deep_link`**(给 cold-start 场景—— Tauri 事件在 listener 没挂时直接 drop,不 queue) |
| `tauri/src-tauri/src/state.rs` | `AppState` 加 `pending_deep_link: Arc<StdMutex<Option<String>>>` |
| `tauri/src-tauri/src/commands/deep_link.rs`(新) | `#[tauri::command] consume_pending_deep_link()` —— 返回 + 清 buffer,frontend 一次性消费 |
| `tauri/src-tauri/src/commands/mod.rs` | 注册 deep_link 模块 |
| `frontend/src/lib/tauri.ts` | 加 `consumePendingDeepLink()` 包装 `invoke('consume_pending_deep_link')` |
| `frontend/src/App.tsx` | `useEffect` 在 Tauri 下:(1) 一次性 drain pending(cold start)(2) listen `deep-link-received`(hot)—— 都解析 URL → `navigate('/app/templates/install' + query)` |
| website `app/templates/[slug]/InstallButton.tsx` | 加 "Desktop App" 菜单项 + 用 `visibilitychange` / `blur` + 2s timer 做"app 没打开"fallback |

所有 mirror md 同步:
- `.mindflow/mirror/tauri/src-tauri/src/lib.rs.md`
- `.mindflow/mirror/tauri/src-tauri/src/state.rs.md`
- `.mindflow/mirror/tauri/src-tauri/src/commands/_overview.md`
- `.mindflow/mirror/tauri/src-tauri/src/commands/deep_link.rs.md`(新)
- `.mindflow/mirror/frontend/src/App.tsx.md`

### 全链路成功路径(预期)

```
浏览器点 "Install · Desktop App"
       ↓ window.location.assign("narranexus://install?url=...&sha256=...")
       ↓ + 启 2s fallback timer(visibilitychange / blur)
macOS Launch Services 查注册表 → 找到 /Applications/NarraNexus.app
       ↓
OS 启动(或唤起)NarraNexus 实例 → 焦点切走 → fallback timer 看到 visibility hidden → 取消显示
       ↓
single-instance plugin:已运行 → 把 URL 转给现有实例;未运行 → 启动后 deep-link plugin 接管
       ↓
tauri-plugin-deep-link → on_open_url callback(lib.rs setup 注册的)
       ↓
       ├── emit "deep-link-received" event(payload = URL string)
       └── 写 AppState::pending_deep_link
       ↓
前端 App.tsx 的 useEffect:
       ├── hot:listenTauri("deep-link-received") 接到 → handleUrl
       └── cold:consumePendingDeepLink() → handleUrl
       ↓
handleUrl: new URL("narranexus://install?...") → host==="install" → navigate("/app/templates/install" + search)
       ↓
BundleImportPage(urlMode):auto POST /api/bundle/import/from-url
       ↓
后端 sidecar uvicorn 流式 fetch URL → preflight → 返回 token + manifest
       ↓
review 页 → 用户点 Import now → confirm → 6 个 agent 装好
```

### 已知 gotcha(测试时盯紧的)

| Gotcha | 触发条件 | 兜底 |
|---|---|---|
| Tauri dev mode (`cargo tauri dev`) 不在 Launch Services 注册 scheme | 只在 `bash scripts/build-desktop.sh` 装到 /Applications 后才有效 | 接受,只用 build 测 deep-link |
| `narranexus://install` 的 `install` 是 host 还是 path | JS `new URL()` 把 `narranexus://install?...` 的 `install` 当 host | App.tsx handleUrl 三种 shape 都接(host / pathname `/install` / pathname `install`) |
| 8000 端口冲突 | 同机器有 `bash run.sh` + DMG 同时跑 | port_preflight 已经会弹原生 dialog;文档强调测前先 stop 其中一个 |
| 单 instance plugin 没加 deep-link feature | 二次 launch 拿到 URL 但 forward 不到现有实例,起了两个 | Cargo.toml 已加 `features = ["deep-link"]` |
| Ad-hoc 签名首次 Gatekeeper 警告 | 第一次开 DMG | 右键 Open 一次 |
| Capability 漏 `deep-link:default` | 运行时报权限错 | 已加 |

### 没做的

- DMG 检测 fallback 在 Linux/Windows 的兼容(我们 bundle target 只有 dmg+app,目前 macOS only)
- Resend / SES 等正式 SMTP 选型(跟 invite-code 那一摊一起放后面)
- 模板分享回流(Phase 3a):用户从 app 内 share 自己的 agent 成 template,share-safe export + 上传

### Next step

- [ ] `bash scripts/build-desktop.sh` 打 DMG(ad-hoc 签名)
- [ ] 装到 /Applications/,首次右键 Open
- [ ] `plutil -p /Applications/NarraNexus.app/Contents/Info.plist | grep -A 5 URLTypes` 验证 scheme 注册
- [ ] 浏览器测 `narranexus://install?url=...&sha256=...` cold-start 路径
- [ ] 已运行时再点 link 测 hot 路径(single-instance forward)
- [ ] website fallback timer 检测(没装 dmg 时显示 fallback message)
- [ ] 走通后 commit + push template_sharing_2026_05_18 on both repos
