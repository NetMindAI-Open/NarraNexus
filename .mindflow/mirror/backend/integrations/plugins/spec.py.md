---
code_file: backend/integrations/plugins/spec.py
last_verified: 2026-09-07
stub: false
---

# spec.py — 插件安装的不可变数据契约

## 2026-09-03（批 1）— `InstallComponent` 改为契约层的那一个

原来这里有一份与 `narranexus.contracts.framework.InstallComponent` 字段相同的副本，`registry.py`
还要逐个转换；现在本文件直接 re-export 契约类型，`PluginSpec.components` 就是 `FrameworkInstall`
里的同一批对象（`test_backend_installer_table_is_derived_from_the_registry` 用 `is` 钉住）。
安装器签名不变。

## 为什么存在

`InstallComponent` 和 `PluginSpec` 是整个安装子系统唯一的"事实描述"层：
一个插件由哪些安装动作组成、每个动作钉的是哪个版本、怎么判断"已装"、给
用户看哪个版本号。把这层单独摘出来（而不是让 `registry.py` 里直接堆
dict）是为了让 `_installers/` 和 `service.py` 都对着同一套强类型字段编程，
而不是各自约定 key 名字。

## 这个文件不做什么

不含任何安装/探测逻辑——那是 `_installers/` 的职责。也不含具体的两个插件
是谁——那是 `registry.py` 的职责。本文件纯数据形状。

## 上下游关系

- **被谁用**：`registry.py` 的 `_spec_from_meta` 把每个带安装配方的框架 `FrameworkMeta` 变成一条 `PluginSpec`；`_installers/*.py` 的
  `install/detect/uninstall` 签名都接收 `InstallComponent`；`service.py`
  遍历 `PluginSpec.components` 派发给对应 installer。
- **依赖谁**：`narranexus.contracts.framework.InstallComponent`（re-export）与标准库 `dataclasses`/`typing`；
  它是这个包里最底层的文件，不 import 包内其它模块。

## 设计决策

- 两个 dataclass 都 `frozen=True`：安装契约在一次请求的生命周期里不该被
  任何环节改写（谁改了 requirement 字符串，pip/npm 实际装的版本就和
  registry 声明的对不上,这是要绝对避免的一类 bug）。
- `InstallComponent.requirement` 是"随时可以整串传给 pip/npm 的成品字符串"
  而不是拆开的 name+version 字段——迫使唯一会拼版本号的地方
  （`registry.py`）负责拼对,`_installers/` 只管转发,不重新拼接。

## 相关约束

- 铁律 #9 —— 框架/LLM 不绑定：`PluginSpec` 用 `framework_name` 字段承接
  `agent_framework.plugin_paths._FRAMEWORK_PACKAGE` 的 key,两边靠这个字符
  串对齐,而不是互相 import 对方的常量。

## 2026-09-07 — PluginSpec 增加 login_marker（B6）

从 FrameworkMeta.login_marker 派生，服务层据此做登录文件探针；probe_package 的注释改指 FrameworkInstall.probe_package（plugin_paths 不再持表）。
