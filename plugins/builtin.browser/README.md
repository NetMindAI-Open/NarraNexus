# builtin.browser

`BrowserModule` 让 Agent 操作真实浏览器，用户可在应用内观看所有标签页，
也可接管后输入、导航、新建和关闭标签页。

## 浏览器与会话

本地设置可选择托管的 Chrome for Testing 或已安装的 Google Chrome。
默认来源为托管浏览器，运行时不随应用打包；选择系统 Chrome 后无需下载
托管运行时。所有入口先检查所选浏览器是否可用，缺失时给出安装或设置提示。
运行不依赖 Playwright Python 包。

默认使用无头模式。有些网站的登录或验证流程不支持无头访问，遇到浏览器
不受支持或验证错误时，可手动开启有头模式，重新启动浏览器会话后重试。
有头模式会额外显示原生浏览器窗口，应用内仍显示页面流；原生窗口嵌入不在
本次实现范围内。云端固定使用托管浏览器和无头模式。

来源与模式变更只作用于新会话，不中断正在执行的任务。每个 Agent 使用
独立持久化配置目录，不读取用户日常 Chrome 配置；系统 Chrome 与托管浏览器
的登录状态分别保存。同一来源切换有头或无头模式保留登录状态。

## 访问与接管

- 所有 HTTP(S) 网页免站点访问授权，没有允许、禁用或例外规则列表。
- 任意脚本执行的 `full_cdp_access` 独立授权仍保留，普通导航和页面操作不走此授权。
- Agent 打开的标签页和弹窗都会展示；旁观者切换查看不会改变 Agent 的操作目标。
- 用户接管后可通过 `+` 新建标签页、输入网址导航、通过关闭按钮关闭标签页。
  关闭最后一页会保留一个空白页，交回控制后 Agent 在当前活动页继续操作。
- 人工接管期间 Agent 等待交回控制，不设置接管时长上限。

## 安装与恢复

`run.sh` 与桌面版默认安装目录均为 `~/.narranexus/browser`。
`NARRANEXUS_BROWSER_HOME` 可覆盖该位置，相对路径以用户主目录为基准。

`NARRANEXUS_BROWSER_DOWNLOAD_HOST` 接受 HTTPS 镜像基础地址，替换下载主机但
保留厂商路径。版本索引也需镜像时，使用 `NARRANEXUS_BROWSER_MANIFEST_URL`
指定完整 HTTPS 索引地址。不会猜测或自动选择公共镜像，也不读取
`PLAYWRIGHT_DOWNLOAD_HOST`。

同一平台、版本和下载来源下支持断点续传。安装时保留 macOS framework
符号链接，并在发布运行时前校验可执行文件版本。API 与 MCP 进程共享安装
进度和取消状态；安装保留现有配置目录，已验证可用的托管运行时不会重复下载。

手动入口与界面使用相同安装器，不启动或重启应用服务。初始化过的源码环境：

```sh
.venv/bin/python -m narranexus.platform.browser install
.venv/bin/python -m narranexus.platform.browser status
.venv/bin/python -m narranexus.platform.browser cancel
```

桌面版安装于 `/Applications` 时使用应用自带 Python：

```sh
"/Applications/NarraNexus.app/Contents/Resources/resources/python/bin/python3" -m narranexus.platform.browser install
```

应用移到其他位置后需使用实际路径。打包的命令无需另装系统 Python、uv 或
Playwright。`status` 输出 JSON 诊断和 `manual_install` 恢复命令；恢复命令
使用当前解释器的精确路径、实际安装目录和下载配置，不会创建缺失的运行时。
程序调用入口是 `_browser_impl.install.manual_install_help()`，Windows 使用
PowerShell 引用，macOS/Linux 使用 POSIX shell 引用。

两种安装方式都允许在动作后传入 `--root`、`--manifest-url` 和
`--download-host`。后两项只覆盖本次命令的环境配置，需要真实、完整的 HTTPS
地址。下载基础地址后会附加完整厂商路径，包括原地址中的
`/chrome-for-testing-public/`。`manual_install_help(manifest_url=..., download_host=...)`
会根据提供的实际地址生成可复制命令。

CLI 以逐行 JSON 输出进度及最终结果，包含 `ok`、`error`、运行时状态和恢复命令。
退出码：成功为 0，安装失败为 1，参数错误为 2，Ctrl-C 为 130。
重复运行相同安装命令可续传，`cancel` 也可取消其他应用进程持有的安装任务。

## 验证入口

后端测试位于 `tests/browser/`，模块集成测试位于
`tests/module/test_browser_module_integration.py`，桌面与源码打包契约位于
`tests/release/test_browser_packaging.py`。前端的浏览器设置、页面流、标签页和
输入回归测试与组件同目录。真实 Chrome 测试需按
`tests/browser/test_live_browser_e2e.py` 的环境开关启用，使用独立临时配置目录。
