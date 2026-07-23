# Skill Marketplace — MVP 5 技能选定 + 本地全链路试跑(2026-07-21)

## Trigger
Hongyi 提供《NarraNexus Skills — Source & Installation Summary》PDF,要求从中选 5 个可安装的 MVP 技能试跑 marketplace 全链路;并确认 dev server 的 S3 可用性。

## 选定的 5 个技能(全部扫描 passed)
| skill_id | clawhub 来源 | 类别 | 理由 |
|---|---|---|---|
| ddg-search | jakelin/ddg-web-search | fallback | 免费网页搜索,零依赖 |
| markdown-converter | steipete/markdown-converter | utility | PDF/DOCX/XLSX→MD,文档推荐 default |
| gh | trumppo/gh | integration | ⭐ must-have,GitHub CLI |
| api-tester | wanng-ide/api-tester | utility | 开发类(ggshield 的替补) |
| chain-of-density | killerapp/chain-of-density | utility | 纯 prompt 方法论,零依赖对照组 |

打包件(zip + manifest.json,剥除 clawhub `_meta.json`):`~/Desktop/xyz_proto_test/NarraNexus_mvp_skill_packages/`

## 关键发现
1. **ggshield-scanner(⭐)被自家 Gate 拒**:11 个 HIGH——安全类技能文档合法讨论 `.env`/凭证路径,README 还有真 `curl|bash` 安装指引。Gate 工作正常但暴露规则课题:文档 vs 代码的敏感路径分级(候选方案:.md 降 WARN、代码维持 REJECT)。
2. **真 bug 修复**:cloud 全局 auth 中间件把 marketplace 读端点也 401 → 桌面端(无 cloud JWT)拉不到目录。修复:GET `/api/marketplace/skills/*` 可选认证(带凭证解析身份,匿名放行降级)+ `/publish` 自带 token 豁免。见 backend/auth.py mirror 2026-07-21 条目。
3. **dev server 无 S3**:无 `~/.aws`、stack `.env` 无 AWS key(仅 RDS 域名)、实例无 IAM role。S3 需在 AWS 账户新开 bucket + 发 key。本地回退路径已验证:未配 `SKILL_S3_BUCKET` 时 artifact 落 `<base_working_path>/../marketplace_store/`(真实部署即 `~/.nexusagent/marketplace_store/`)。
4. clawhub CLI 安装格式是 `owner/name`(非文档里的连字符 slug),匿名有 ~60s 限流。

## E2E 结果(隔离环境,未触碰真实数据)
registry 实例(NARRANEXUS_DEPLOYMENT_MODE=cloud + 临时 sqlite + 本地 store,:8001)→ `scripts/publish_skill.py` 发布 5 个 → 匿名 search 可见 → 桌面路径(RemoteMarketplaceSource)hash 校验安装到测试 workspace → skills/ 落盘与 builtin 并存、审计行 source=marketplace/版本正确 → 服务端下载计数 +1。

## Next step
① AWS 开 bucket + key → cloud 注入 `SKILL_S3_BUCKET`/`MARKETPLACE_PUBLISH_TOKEN`/`SKILL_SECRETS_KEY` → 重发 5 个包;② DMG + run.sh 双模式手工验收;③ 上架前确认 clawhub 来源技能的 license/署名展示;④ 敏感路径规则分级讨论。
