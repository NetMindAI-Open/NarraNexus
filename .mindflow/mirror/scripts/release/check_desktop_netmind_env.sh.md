---
code_file: scripts/release/check_desktop_netmind_env.sh
last_verified: 2026-09-09
stub: false
---

# scripts/release/check_desktop_netmind_env.sh — DMG 的 NetMind 端点闸门

## 为什么存在

B-40：DMG 的 Power 登录端点在构建期烤入（前端 `VITE_*` 经 vite，后端变量经 cargo `option_env!`），空值会静默落回编译期 protago-dev 默认——丢一个 repo Variable 就发出 dev OAuth 应用，构建仍绿。本脚本把这类问题变成构建失败。

## 两个子命令

- `env`（build-desktop.sh 第一步，下载/构建之前）：`VITE_ENABLE_POWER_LOGIN` 与 `NARRANEXUS_ENABLE_POWER_LOGIN` 必须一致；`NARRANEXUS_REQUIRE_POWER_LOGIN` 为真（工作流在 tag 上设）时 Power 必须开；开着时 `VITE_NETMIND_{AUTH_API,ACCOUNTS_URL,SYS_CODE,REGISTER_URL}` + `NETMIND_AUTH_API_URL` / `BILLING_API_BASE` / `NETMIND_KEY_API_BASE` / `NETMIND_INFERENCE_BASE` 全必填；URL 必须 https、主机为 `netmind.ai` 或 `*.netmind.ai`、且不含 `test`/`dev`/`staging` 标签（`test.api.netmind.ai` 是 dev 推理栈）；前端与后端 auth 必须同主机。Power 关着（社区构建）不要求任何端点。
- `bundle <dist>`（`npm run build` 之后）：`grep -rlF protago-dev`，有命中即失败并列出文件。

## 约束

- 纯 bash 3.2（macOS runner 的 /bin/bash）：不用关联数组、不用 `${v,,}`，间接取值用 `eval`，变量名来自脚本内常量表。
- 所有错误一次性列全再退出（`::error::` 前缀，Actions 里直接标红），不是见一个退一个。
- 故意没有"允许非 prod"的逃生口：内部要 dev 版 DMG 的需求出现时再显式设计。

## 测试

`tests/release/test_desktop_netmind_env_guard.py` 在干净环境里直接跑本脚本（全 prod 通过、社区构建通过、tag 缺 Power 失败、逐个缺端点失败、非 prod 主机失败、前后端不一致失败、bundle 命中/干净/缺目录），并断言 build-desktop.sh 的调用顺序（env 门 < npm run build < bundle 门）与工作流 tag 上的 REQUIRE 设置。
