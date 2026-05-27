# Repo Governance — 落地报告

> 分支：`chore/repo-governance` （未合并 main）
> 日期：2026-05-18
> 范围：NarraNexus 仓库治理首批文件落地、AI-assisted contributing 工作流落地、
> 给负责 README 重写的同事的 advice 文档。

---

## 1. 这次解决的问题

Bin哥给的三条核心需求：

1. **扩大社区规模** — 让贡献者能轻松加入
2. **保留一定规范** — main 不能因为开源就乱
3. **让贡献者用得上三级文档 + CLAUDE.md，并继续维护它** — 这是 NarraNexus 独有
   的竞争力

> 核心洞察：在 AI-coding 普及的今天，"规范多 = 门槛高"这条不再成立——只要
> 我们把"规范在哪、文档在哪、怎么用"讲清楚，AI 会替贡献者执行规范。三级文档
> 体系不再是负担，而是给贡献者的 AI 的"超高质量项目语境"。

## 2. 设计思路 — 一句话

> **不教规范，让 AI 替贡献者遵守规范。我们只做"指路牌"。**

具体做法：

- README / CONTRIBUTING 顶部明确告诉用户 → CLAUDE.md / `.mindflow/` / AGENTS.md 的存在
- AGENTS.md 是面向所有非 Claude AI editor 的 vendor-neutral pointer，与
  CLAUDE.md 内容对齐（CLAUDE.md 是 source of truth）
- mirror md 同步规则（铁律 #10）写进 PR 模板和 CI 检查里，但 **非 blocking**
  —— 第一次 PR 不该卡在文档同步上，maintainer 兜底
- 治理流程（CODEOWNERS / squash-merge / Conventional Commits）通过自动化
  workflow 落地，不靠 contributor 记住

## 3. 完成的交付物

### 3.1 顶层文档（根目录）

| 文件                  | 状态  | 说明                                                              |
| --------------------- | ----- | ----------------------------------------------------------------- |
| `CONTRIBUTING.md`     | 重写  | 205 行 → 110 行；顶部 §0 是 AI-assisted briefing；§0.1 解释铁律 #10；下面是 issue / PR / 命令快捷；非 coding 用户在顶部 callout 引导到 §1 |
| `AGENTS.md`            | 新建  | [agents.md](https://agents.md/) 规范文件名（复数）。仅一段 redirect 到 `CLAUDE.md`，保持单一 source of truth |
| `CODE_OF_CONDUCT.md`  | 新建  | Contributor Covenant 2.1 标准条款                                 |
| `SECURITY.md`         | 新建  | private vulnerability reporting + in-scope / out-of-scope         |
| `GOVERNANCE.md`       | 新建  | 角色 / merge 规则 / 第二审查清单 / 如何成为 maintainer / 如何 step down |
| `MAINTAINERS.md`      | 新建  | 故意不暴露个人 email；走 GitHub Discussions / private advisory    |
| `README_advice.md`    | 新建  | 给负责 README 的同事的 7 条具体建议（位置 + 文案 + 理由）       |
| `.mailmap`            | 新建  | 把 Bin Liang 三个 alias / Hongyi Gu 两个 alias / Chengyu Huang 合并为单一 contributor |
| `REPO_GOVERNANCE_REPORT.md` | 新建 | 这个文件 |

### 3.2 .github 目录

| 文件                                          | 类型 | 作用                                                          |
| --------------------------------------------- | ---- | ------------------------------------------------------------- |
| `.github/ISSUE_TEMPLATE/01-bug-report.yml`    | 新建 | GitHub Forms 格式，含 OS / install method / log 位置分流      |
| `.github/ISSUE_TEMPLATE/02-feature-request.yml` | 新建 | scope multi-select + 简短问题陈述                             |
| `.github/ISSUE_TEMPLATE/03-question.yml`      | 新建 | 鼓励先走 Discussions                                          |
| `.github/ISSUE_TEMPLATE/04-good-first-issue.yml` | 新建 | maintainer-only 模板，用来产出 good-first-issue              |
| `.github/ISSUE_TEMPLATE/config.yml`           | 新建 | 禁用 blank issue + contact links（Discussions / advisory / CONTRIBUTING） |
| `.github/pull_request_template.md`            | 更新 | 加 Tier-2 mirror md sync section + `Mirror-md: needs-maintainer` 兜底 |
| `.github/CODEOWNERS`                          | 新建 | 把 CLAUDE.md / `.mindflow/` / 认证 / bundle / schema / release 钉到 lead maintainer |
| `.github/labels.yml`                          | 新建 | 12 个标签的 source of truth（5 type + 4 state + 3 closing）   |
| `.github/dependabot.yml`                      | 新建 | GitHub Actions + npm (frontend) + pip 每周一更新，跳过 major bump |

### 3.3 Workflows

| 文件                                          | 触发                          | 行为                                                          |
| --------------------------------------------- | ----------------------------- | ------------------------------------------------------------- |
| `.github/workflows/welcome.yml`               | 第一次提 issue / PR           | 双语欢迎，提示 mirror-md 流程                                 |
| `.github/workflows/stale.yml`                 | 每天 06:00 UTC               | issue 60d 无响应 stale，再 14d close；PR 30+14d；exempt: `pinned`/`security`/`help wanted` |
| `.github/workflows/semantic-pr-title.yml`     | PR opened / edited            | 检查 Conventional Commits title，错误时给具体例子            |
| `.github/workflows/labels-sync.yml`           | push to main 且改了 labels.yml | 自动同步 labels.yml 到仓库标签（不删 manual labels）          |
| `.github/workflows/mirror-md-check.yml`       | PR opened / synchronize       | 改 `.py/.ts/.tsx/.rs` 但没同步 mirror md → bot 友好评论，**不 block** |

### 3.4 三级文档系统补强

| 文件 | 状态 | 说明 |
|------|------|------|
| `.mindflow/project/playbooks/README.md` | 新建 | 之前这个目录被 CLAUDE.md / CONTRIBUTING 大量引用但**不存在** —— Round 1 cold-read 致命问题。建 placeholder + 列出 10 个待写 playbook 名字 + fallback 规则 |
| `.mindflow/project/playbooks/add_new_module.md` | 新建 | 第一个真正的 playbook：完整 SOP（10 步 + checklist + 常见错误），用 `EmailModule` 当 worked example |

### 3.5 其他

- `.gitignore` —— 把历史遗留的 `AGENTS.md` 行删掉（新 AGENTS.md 是要 track 的）

## 4. 我没动的东西

- **`CLAUDE.md`** —— 铁律 #11，只 Owner 改。Round 1 / 2 都发现 CLAUDE.md 是
  中文 + 内嵌 "Superpowers" 等内部概念可能让外部贡献者困惑，但应由 Owner
  亲自决定要不要双语 / 清理。AGENTS.md 里我加了三段缓冲（语言提示 / "三个名字"
  解释 / common tasks 英文索引）来弥补
- **`README.md`** —— 同事在改。所有建议都写到 `README_advice.md`
- **现有 workflows `ci.yml` / `build-desktop.yml`** —— 工作的不动
- **`/reference/` 目录** —— 是 Bin哥 个人 notes，gitignored

## 5. 三轮 cold-read 验证总结

每轮用不同 persona / 不同任务派 explorer agent 假装新贡献者走 pipeline：

### Round 1 — 报 bug 的全新外部用户

发现的 critical 问题（都已修复）：

1. CONTRIBUTING.md clone URL vs README clone URL 是两个 org → 加了"两个
   GitHub org 一个项目"的解释段落
2. MAINTAINERS.md 没有 contact info → 加了 Contact section 走 GitHub 通用渠道
3. `.mindflow/project/playbooks/` 被到处引用但不存在 → 建了目录 + placeholder
   README + 第一个 playbook (`add_new_module.md`)
4. README 没 link CONTRIBUTING → 写进 `README_advice.md` 给同事
5. "三个名字"（NarraNexus / xyz_agent_context / NexusAgent）→ AGENTS.md 顶部
   加了解释段落

### Round 2 — 想加 Module 的 Cursor 开发者

explorer agent 中断没完整产出。我自己做 mental walk-through，发现：

- AGENTS.md 没具体 "add a new module" 指引 → 加了 Common tasks 索引段
- `add_new_module.md` playbook 缺失 → 建了完整版（10 步 + checklist）
- CLAUDE.md 「新建 Module 步骤」是中文，Cursor 用户读不顺 → playbook 用英文
  写一遍 + AGENTS.md 顶部加 CLAUDE.md 语言提示

### Round 3 — 完全没用 AI 的 bug-reporter

发现的问题（都已修复）：

1. issue picker 里的 "Read the docs first" 描述误导 bug 用户 → 改成
   "Thinking of contributing code? Read this first" + 说明 "bug 用户不需要看这个"
2. bug template 没说 desktop crash log 在哪 → 加了五种 install 方式对应
   log 位置
3. CONTRIBUTING.md 顶部没有 "bug 用户跳到 §1" 提示 → 加了 callout
4. README 没 above-the-fold "Report a bug" 链接 → 写进 `README_advice.md`

## 6. 希望开发者怎么用

### 6.1 第一次来贡献的外部开发者

**理想路径：**

1. 打开 `README.md` → 看到 "AI-assisted contributing" pointer（待同事加，
   见 `README_advice.md` #1）
2. 点击进 `CONTRIBUTING.md` → 读 §0 30-second briefing，知道把 `CLAUDE.md`
   和 `.mindflow/_overview.md` 喂给自己的 AI editor
3. AI 自动遵守 binding rules，写代码时按命名 / 架构 / 编码规范走
4. 改完代码 AI 自然会更新 mirror md（鉴于 CLAUDE.md §10 在 context 里）
5. 开 PR，PR template 引导填 verification + mirror md 状态
6. CI（mirror-md-check）非 blocking 评论；welcome bot 友好回应
7. 一个 maintainer review，squash-merge

### 6.2 想加 Module 的开发者

新建的 `add_new_module.md` 是端到端 playbook，AI 跟着 10 步走就能完工。
playbook 里把"必须做"和"如果时间紧让 maintainer 帮你"分开列。

### 6.3 想报 bug 的非 coding 用户

issue picker 直接列 4 个模板，blank issue 禁用。bug template 已经按
install method 给具体 log 位置；不会被 CONTRIBUTING 的 AI 内容拦住
（顶部有 callout）。

### 6.4 想升级为 maintainer

`GOVERNANCE.md` "How to become a maintainer" 给了具体 bar（5+ PR / 3+
triage / 7d 提名静默通过）。

## 7. 已知 limitation / 后续 TODO

1. **CLAUDE.md 语言** — 中文，需要 Owner 决定是否双语 / 是否分内外
   两份。AGENTS.md 已经做了英文 buffer，但根上的问题在 CLAUDE.md
2. **`.mindflow/project/playbooks/` 只有 1 个真正的 playbook**
   （`add_new_module.md`）+ 1 个 README placeholder。CLAUDE.md 列了
   10 个 playbook 名字，剩 9 个等社区 / maintainer 补
3. **`.mindflow/project/references/` 也只有 3 个 ✅，剩 4 个标了
   stub** — Phase 2 工作，未在本次治理范围
4. **CODEOWNERS 只钉了 lead maintainer** —— 等其他 maintainer 加入再分
5. **Branch protection 配置在 GitHub UI 上点** —— 我没 GitHub 仓库设置
   权限，需要 owner 手动开 main 的 "Require PR" / "Require status
   checks" / "Restrict force push"
6. **README_advice.md 是 transient 文件** —— 同事改完 README 后应删
7. **没加 CLA / DCO** —— 早期项目不强求，但商业化前要决定

## 8. Branch / merge 计划

本次工作全部在 `chore/repo-governance` 分支上。**不合并到 main** —
Bin哥 你 review 后决定：

- 直接 squash merge 进 main
- 还是改完再合
- 还是拆成多个 PR 分批合（governance docs 一个 PR / .github workflows
  一个 PR / playbook 一个 PR 等）

## 9. 这份报告本身

`REPO_GOVERNANCE_REPORT.md` 是给你（Bin哥）的一次性交付物。它跟
`README_advice.md` 一样是 transient —— 你 review 完 / 拍板完后建议
直接删，避免污染仓库。
