---
code_file: src/xyz_agent_context/agent_framework/providers/free_tier.py
last_verified: 2026-07-28
stub: false
---

# free_tier.py — 免费额度这张「卡」的身份

## 为什么存在

2026-07-28 改造把免费额度从「resolver 里的一条特殊分支 + 自建 token 计量」
变成「一张普通的 provider 卡，钥匙是网关上的美元钱包」。改完之后，整个代码库
里关于免费额度只剩两个必须集中管理的事实，就是本文件：

1. **`FREE_TIER_SOURCE = "netmind_free"`** —— `user_providers.source` 的取值。
   刻意不复用 `netmind`：两张卡必须能**并存**，用户烧完钱包要能切到自己的
   NetMind Power 卡。`UserProviderService` 的「一个 source 一张卡」去重是按
   source 分组的，共用 source 会让两张卡互相顶掉。

2. **`free_tier_endpoints()`** —— 网关的两个 base_url，从环境读。之所以不写进
   `user_service` 的模块级 `_DUAL_PROVIDER_CONFIGS` 常量表：这两个值是
   **部署相关**的（dev/prod 网关不同，本地根本没有网关），常量表表达不了。

## 两个协议为什么是两个不同的 base

Claude CLI 会自己往 base 后面拼 `/v1/messages`，而 OpenAI 客户端要求 base
本身就以 `/v1` 结尾。给两者同一个 URL，必然有一边 404。这两个形状是在 dev
网关上实测跑通后才固化下来的。

## 上下游

- 上游：`backend/integrations/free_tier/provisioner.py` 开钱包后调
  `onboard_one_key(provider_type=FREE_TIER_SOURCE, ...)`。
- 下游：`user_service._build_dual_providers` 建两行；`cloud_policy` 放行它绑
  slot；`model_sync` 把它并入 netmind 的目录刷新。

## 注意

`is_free_tier_enabled()` 天然 cloud-only —— 钱包挂在只有云上才有的网关上。
所有调用方都以这一个 flag 收口，不要在别处再判断一次部署模式。
