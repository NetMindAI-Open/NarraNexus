# Team Marketplace — 设计 + 实现 T1–T6(2026-07-21)

## Trigger
Hongyi:左侧 Marketplace 分 Skills / Teams 两处;Team Marketplace host 现有 team templates,blob 上 S3(暂本地、与 skill 分开)。

## Refs
- 设计: `reference/self_notebook/specs/2026-07-21-team-marketplace-tech-design.md`
- 复用来源: 未合并分支 `ee1db871`(整套 team marketplace)+ 现有 `bundle/importer.py`
- commits: c35db59f(设计)/2fd5af66(A/B 厘清)/4a8e7db8(后端 T1-4)/dbb1e48d(前端 T5)

## Conclusions
1. `ee1db871` 几乎写好全部资产;本项目三改:挂 `/api/marketplace/teams/*` 前缀、blob 从 narra.nexus 改我们 artifact store(独立 prefix)、前端拼成一个 Marketplace 两 tab。
2. **A/B 之争自解**:安装 = fork 到本地库(DMG→sqlite / cloud→RDS),importer 必须本地跑;blob 在云端(DMG 无 S3 凭证)。所以 preflight/confirm 两模式同路径(铁律 #7),只有"取字节"分叉——复用已建好的 skill Local/Remote source 抽象:registry host 直读 store,desktop HTTP 拉云端。非全局二选一。
3. 安装引擎零新增代码:install-preflight 内部 = resolve bundle → 验 sha256 → `importer.preflight`;confirm 复用现有 `/api/bundle/import/confirm`。前端 `?teamTemplate=` deep-link 复用整个导入向导。
4. `team_catalog` 无 installations 审计表——fork 出的 agents/teams 就是记录(team.source=`bundle:<id>`)。

## Evidence
- seed 实测:9/9 官方 template 从 narra.nexus 拉下、验 hash、存 `~/.nexusagent/marketplace_store/teams/`、建 catalog。
- T6 HTTP 端到端(隔离实例):install-preflight gaokao-team → confirm → agents_created=5, team_created=True, instances_created=57;DB 落 team「高考模拟」+ 5 agent,download +1。
- 后端 2856 passed 零回归;前端 tsc/eslint 干净、vitest 18(含 4 新);真 importer 集成测试过。

## Next step
S3 上线后配 `TEMPLATE_S3_BUCKET`(已支持,回落本地);DMG/线上双模式手工验收;社区上传推后。Team Recommended(team_skill_policies 占位表已在)是后续独立方向。
