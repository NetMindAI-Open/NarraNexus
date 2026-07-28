---
code_file: src/xyz_agent_context/marketplace/_skill_marketplace_impl/artifact_store.py
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — review 修复:exists 只吞 404

S3ArtifactStore.exists 改为 catch ClientError 判 404/NoSuchKey,其余错误(限流/网络)re-raise——原先吞所有异常返 False,S3 抖一下就让 seed 幂等闸门每次重传。


## 2026-07-21 — MARKETPLACE_S3_ENV(dev/prod × skills/teams 单桶布局)

`_compose_prefix`:一个 bucket 按 `<MARKETPLACE_S3_ENV>/<skills|teams>` 分环境
(dev/skills, dev/teams, prod/skills, prod/teams)。显式 SKILL_S3_PREFIX /
TEMPLATE_S3_PREFIX 仍优先;不设 env 段则回落扁平默认(narranexus-skills/teams)。
一个部署只需设 SKILL_S3_BUCKET + SKILL_S3_REGION + MARKETPLACE_S3_ENV,
skills/teams 自动分目录、不会设错。


## 2026-07-21 — get_template_store()(Team Marketplace)

新增 team 专用 store 选择器,与 skill 物理分开:S3 走 TEMPLATE_S3_BUCKET
(回落 SKILL_S3_BUCKET)+ TEMPLATE_S3_PREFIX(默认 narranexus-teams);本地
落 marketplace_store/teams/。boto3 仍只此一文件。


# artifact_store.py

Object-storage abstraction for marketplace skill artifacts. **boto3 exists
ONLY in this file** (spec §4, iron rule #9) — swapping S3 for R2/OSS later
touches nothing else. `SKILL_S3_BUCKET` env selects S3 (cloud); otherwise a
filesystem `LocalArtifactStore` under `<base>/../marketplace_store` serves
dev/tests/single-host. The S3 client is created lazily so importing never
needs AWS credentials. LocalArtifactStore path-joins are guarded against
key escapes (`..`).
