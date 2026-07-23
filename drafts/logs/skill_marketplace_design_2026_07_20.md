# Skill Marketplace — 代码调研 + 技术设计 v1.1(2026-07-20)

## Trigger
Hongyi 提供 Phase 1–4 四份方案 PDF(~/Downloads),要求深度代码调研、review 方案、讲清 skill 存取逻辑与 local/cloud 区别,随后起草可实施的修订版设计。

## Refs
- 产出: `reference/self_notebook/specs/2026-07-20-skill-marketplace-tech-design-v1.1.md`
- 输入: 四份 Phase PDF(Phase 3 PRD v0.2 Code-Aligned + Phase 4 v1.0)
- 代码: `module/skill_module/`(skill_module.py, _skill_mcp_tools.py 端口 7806)、`bundle/skill_backup.py`、`backend/routes/skills.py`、`utils/deployment_mode.py`

## Conclusions(关键事实与决策)
1. **现状**: 已安装 skill 无 DB 表,纯文件系统(`workspaces/{agent}_{user}/skills/` + `.skill_meta.json`);`skill_archives` 表只是备份登记,不是目录。
2. **方案主线正确**: S3 存 artifact + DB 目录 + 7 步 Install Engine 扩展 SkillModule,Runtime 零改动(装完与手写 skill 同构)。
3. **修订决策**(Hongyi 拍板 1–3,AI 定 4):
   - ① 不用 PG/alembic/Celery/Redis → 复用 schema_registry 双方言 + auto_migrate + services/ poller。
   - ② Marketplace 统一命名空间、按对象拆两个子前缀:`/api/marketplace/skills/*`(本项目)+ `/api/marketplace/teams/*`(预留 agent/team 分享,未来承接 `feat/in-app-marketplace`);两者独立演进。
   - ③ 磁盘=唯一真相,DB=审计跟随;三道防线(唯一写路径 InstallPipeline + Prompt 禁手工动 skills/ + 对账 poller,disk wins、只写 DB 不动文件)。
   - ④ env_config 从 base64 升级 Fernet 真加密(local 密钥文件 0600 / cloud 环境变量 `SKILL_SECRETS_KEY`),旧值惰性迁移。
4. **v1.0 的其他修正**: 删 S3 registry-index.json(DB 是唯一目录,消除一致性风险);skill_installations 唯一键需 (agent_id, user_id, skill_id) 三元组;`.skill_meta.json` 的 source_type/source_url/installed_at 已存在,只新增 hash/content_hash/updated_at;人天估算违反铁律 #17 已改结构维度。

## Evidence
- SkillModule docstring 自述"唯一用文件系统管理状态的 Module";`ALWAYS_LOAD_MODULES` 成员。
- 代码库 grep 无 boto3/S3;`scaling_assumptions.md` 全部标 SINGLE-WORKER ASSUMPTION。
- `feat/in-app-marketplace`(ee1db871)未进 dev;工作树残留其 `__pycache__` .pyc(无源文件,排查时勿误判)。
- 三处 `is_cloud_mode` 实现,权威为 `utils/deployment_mode.get_deployment_mode()`。

## Next step
按 v1.1 §11 分段顺序实施:① 表+repository+secret_box → ② Scanner → ③ Pipeline 接线(唯一动现有行为段)→ ④ Registry+S3 → ⑤ API+MCP → ⑥ 对账器 → ⑦ 前端 → ⑧ MVP skills + 双模式 e2e。TDD;每个新文件配 mirror md。
