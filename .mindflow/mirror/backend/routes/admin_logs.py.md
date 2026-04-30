---
code_file: backend/routes/admin_logs.py
last_verified: 2026-04-28
stub: false
---

# admin_logs.py — Operator-facing log inspection endpoints

## 为什么存在

T16 的产物。云部署（EC2 / Web 模式）原本没法看本地 log——
`~/.narranexus/logs/<service>/` 文件只能 ssh 进去 tail。这一组
endpoints 把目录暴露成 HTTP 接口，前端 SystemPage 的 LogViewer
（T17）和命令行 `curl` 都能用。部署仓库 CLAUDE.md 明确要求
"log 要可以查看所有服务的日志，并且可以下载日志"，本路由覆盖该需求。

## 四个 endpoints

挂载在 `/api/admin/logs` 前缀下：

| Method | Path | 用途 |
|---|---|---|
| GET | `/services` | 列出 `NEXUS_LOG_DIR` 下所有 service 子目录 + 每个的文件元数据（name / size / mtime / 是否压缩） |
| GET | `/{service}/tail?n=&level=&date=` | 拉某 service 当天 log 的后 N 行；可按 level（按 `\| LEVEL    \|` 列匹配）过滤；可指定 YYYYMMDD 切片 |
| GET | `/{service}/download?date=` | 流式返回原始 .log 或 .log.zip 文件 |
| GET | `/event/{event_id}?service=&n=` | 跨 service grep 当天文件里包含 `event_id` 的所有行——这是"按 event_id 拉一次 turn 全链路"的 server-side 实现 |

## 安全约束

1. **path-traversal 防御**：`{service}` 必须匹配 `^[a-zA-Z0-9_\-]+$`，
   `{date}` 必须 `^\d{8}$`，`{event_id}` 长度上限 64 + 字符集白
   名单。其他形态的请求 400。
2. **角色门**：`_require_staff()` 在 cloud mode 下要求
   `request.state.role == "staff"`；local mode（`request.state` 上
   没有 role）直接放行——单机 loopback 单用户信任模型，与项目其他
   admin 路由一致。
3. **不写**：所有 endpoint 都是 GET，没有任何修改文件系统的能力。

## 实现细节

- `_tail_lines(path, n)`：plain `.log` 用 seek-based tail（分块从尾
  部往前读，避免加载整个文件）；`.log.zip`走完整解压（一天的滚动文
  件大小有限，简单实现优先）。
- `_filter_by_level()` 是字符串匹配，依赖 `setup_logging` 的固定
  format `... | LEVEL    | ...`（左对齐 8 字符）。如果 format 改
  了，这里要同步改。
- `_today_log_path()` 用 `datetime.date.today()`，**没有时区处理**
  ——以服务器本地日期为准，与 loguru 的 `{time:YYYYMMDD}` rotation
  一致。

## 上下游

- 依赖 `setup_logging` 写下的目录结构 `<NEXUS_LOG_DIR>/<service>/`，
  以及 Tauri sidecar T18 落盘到同一布局。任何破坏布局的改动都会
  让这些 endpoints 返回 404。
- 前端调用：`frontend/src/lib/platform.ts` 的 `WebBridge.getLogs()`。
- 路由注册：`backend/main.py` 的 `app.include_router(admin_logs_router,
  prefix="/api/admin/logs", ...)`。

## Gotcha

- 如果换了 JSON format（`NEXUS_LOG_FORMAT=json`），`level` 参数的
  字符串匹配会失效，因为 JSON 行没有 `| LEVEL    |` 列。这是 v2 的
  事——届时 admin_logs 也要支持 JSON 解析。
- `/event/{event_id}` 默认只扫**今天**的文件，不回溯历史。要查昨
  天的请加 `service=` 然后用 `/{service}/download?date=...` 自己
  zgrep。这是有意的——避免一个慢请求把所有压缩文件解一遍。
