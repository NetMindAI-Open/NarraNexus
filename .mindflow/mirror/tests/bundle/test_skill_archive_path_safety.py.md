---
code_file: tests/bundle/test_skill_archive_path_safety.py
last_verified: 2026-08-18
stub: false
---

# test_skill_archive_path_safety.py — SEC-07 归档路径的写侧 + 读侧

## 为什么存在

SEC-07 是一个被 QA 实证的路径穿越：`skill_archives/{user_id}/{skill_name}.*`
的拼接散在 7 处，`skill_name` 全部来自进程外部。修复方式是收敛成
[[skill_backup.py]] 的 `archive_target()` 单一构造点，所以测试也钉在这个
构造点上，而不是逐个 caller 重复一遍参数化。

## 覆盖的五件事

1. **写侧构造点**：10 个穿越 payload（含 QA 用的
   `../qa-sec07-oneup-marker` 原件）、3 种 suffix 的正常路径、穿越型
   `user_id`、symlink 出逃。symlink 那条专门盯
   `ensure_within_directory` 锚在用户目录上的盲区。
2. **不许再手拼**：一条 grep 式断言扫 bundle 路由 + 整个 `bundle/` 包，
   禁止出现"看起来在拼路径、且同行提到 `skill_name`"的行（`/`、
   `.joinpath(`、`os.path.join(`）。**实参位置的拼接也算**——
   `copy2(src, base / f"{skill_name}.zip")` 会被抓到，不只是赋值语句。
   豁免只有两类、按形状不按行号：sanctioned builder（`archive_target` /
   `prepare_archive_target`）和日志/标签行（`logger.` 开头，或 f-string 喂
   给 `append(`，`f"{skill_name}@{old_aid}"` 就在这一类）。
   演化史（两次都是"守卫比它自称的窄"）：v1 只匹配 `f"{skill_name}`，漏
   `.joinpath` / `os.path.join`，且扫描面写死 3 个文件；v2 放宽了这些但**新
   加了一道"必须赋给 tgt/out/target/…"的过滤**，反而对实参位置和无空格
   `base/f"…"` 比 v1 更窄，而 docstring 没写这道过滤（mirror 写了，两边打
   架）。v3 去掉赋值要求、改成排除已知误报，docstring 与本文件同步。
   验过：往 importer 注入一行实参位置的拼接，v3 会红。
3. **读侧，三种形状**（初版只有第一种，是个真空档）：
   - root **之外**的绝对路径 —— `/export` 那条读侧漏洞的形状
   - root **散装层**（`{root}/marker.zip`）—— **dev 库 id=20 的真实形状**，
     因为存的字符串 `{root}/{uid}/../marker.zip` resolve 之后就在 root 里
   - **别人的用户目录**（`{root}/other_user/x.zip`）—— 跨用户读

   后两条在 root 锚点的判据下会放行，初版恰好只测了第一条，所以"读侧兜住
   id=20"这句话既没被代码实现、也没被测试发现。三条都断言 canary 不进包 +
   warning 提到该 skill + manifest 无 `archive_ref`；另有正向用例保证守卫
   没把正常 zip 导出打死。
4. **导入侧归档登记**：`test_imported_zip_skill_registers_archive` 走完整
   export → 删行 → preflight → confirm，断言 `skill_archives` 行被重新写
   出来。钉的是 `SameFileError` 那个既有缺陷（见 [[importer.py]]）。

## Gotcha

- `archives_root` fixture monkeypatch 的是
  `skill_backup.SKILL_ARCHIVES_ROOT` 模块级常量（`_user_archive_dir` 每次
  调用时才读它），不是 `Path.home()`。真实 `~/.nexusagent` 不会被碰到。
- 读侧用例复用 [[test_skill_import.py]] 那套 `db_client` /
  `tmp_workspace_root` fixture 组合（隔离 sqlite + 覆盖
  `base_working_path` 和 HOME）。
- 三条读侧用例都验过牙口：把 builder 的判断换回
  `is_within_archives_root`，散装层和跨用户两条**会红**、root 外那条仍绿
  （这正是初版漏掉差异的原因）；换成 `if False:` 三条全红。导入侧那条把
  `register_archive` 换回 `backup_after_api_install` 也会红。加新守卫时用
  同样的方式验。
- 形状断言的覆盖面写在它自己的 docstring 里，**故意写成"能挡什么、挡不住
  什么"**：改名变量、跨行拼接它都看不见。它是防复发的窄网，不是不存在
  的证明——别因为它绿就跳过人工核。

## 2026-08-18 四审 — 新增 `skill_dir` 这一整轴（第 5 件事）

导出请求体里的**第三个**客户端字符串（前两个是 `archive_path`、`skill_name`），
同样进文件系统路径，SEC-07 当时漏了它。这一轴有四条：

- `test_export_rejects_a_traversing_skill_dir` —— 8 个 payload × **两种
  install_method**，断言的是**闸口本身**：warning 含精确文案
  `unusable skill_dir`、该 skill 不在 manifest 里。
- `test_full_copy_cannot_pack_another_users_workspace` —— **SEC-08（P0）的回归
  网，单独一条**，只断言一件事：别人的字节不在产物里。

  ⚠️ 为什么必须单独：上面那条先断言 warning 文案，mutation 时**第一行就失败、
  泄漏断言根本不会被执行**——它可以报"12 条红"而 P0 的真实契约无人看守。跨租户
  读的回归网必须**因为字节泄漏而红**，不能因为别的。

  ⚠️ 五审前这条 canary 有**两条独立的断腿**（都记下来，因为都是"因为错误的理由
  变绿"）：
  1. **参数表里没有任何 payload 能解析到 victim**。victim 在
     `{base}/victim_user/…`，而 `../escape` / `..` / `sub/dir` 等全落在自己的
     agent 目录内或不存在的位置。真实形状 `../../..`（解析到
     `base_working_path` 本身）恰恰是表里唯一缺的。
  2. **单层 `z.read(n)` 看不见嵌套 zip 里的明文**：别人的 workspace 是以
     `skills/{aid}/{dir}-full.zip`（deflate）进包的，外层只解一层拿到的是压缩
     字节。`_find_canary` 现在递归一层，且**扫根层成员**——摘掉闸口时逃逸的包
     叫 `..-full.zip`、落在 staging 根，不在 `skills/` 前缀下。

  payload 选 `../../..` 而不是更深的：更深的路径其**输出**文件名的父目录不存在，
  写盘先失败（ENOENT），证明不了任何事。mutation 实测摘掉闸口后报
  `['..-full.zip!victim_user/agent_victim/skills/private/creds.json']`。
  断言写法有讲究：warning 必须含 **"unusable skill_dir"** 这个具体文案，早期版
  本写的是 `"arena" in warnings`——`zip not found` / `archive path escapes` 也含
  "arena"，闸口摘掉照样绿。
- `test_absent_skill_dir_falls_back_to_skill_name` —— 空值**不是**攻击，是"未
  指定"，回落 `skill_name` 才是正确行为。所以参数表里故意没有空串。
- `test_same_skill_dir_on_one_agent_gets_distinct_bundle_filenames` —— 计数器
  后缀，且断言两个 `archive_ref` **各自指向自己的字节**。
- `test_archive_local_zip_enforces_the_shared_gate`（从
  [[test_corrupt_archive_export.py]] 挪过来的）—— 那个文件的主题是"坏归档不该
  拖垮导出"，这条钉的是**入口准入**，属于本文件这条线。

验过牙口：把 `builder.py` 来源点的 `sanitize_filename` 换回原始字符串，**12 条
红**（含 full_copy 的跨租户读）。

