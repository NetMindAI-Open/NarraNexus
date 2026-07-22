---
code_file: src/xyz_agent_context/module/skill_module/builtin_skills/home-assistant-setup/ha_setup.py
last_verified: 2026-07-16
stub: false
---

# ha_setup.py — 一条命令把 HA + 小米接入固化的 onboarding CLI

## 为什么存在

`home-assistant-setup` 内置技能的执行体。把"帮本地/桌面用户装 HA + 接入设备"从一堆手工
docker/curl(且极易踩 homeassistant.local / GitHub 墙 / onboarding 回调 / redirect_uri 的坑)
收敛成一个 agent 只需顺序调用的 CLI。**只服务本地/桌面**——云端用户自带已暴露的 HA、手填
URL+token,不走这个。

## 关键点

- **只有一处人工不可代劳**(小米登录授权),用 ACTION_REQUIRED 协议表达(打印 `ACTION_REQUIRED:` +
  退出码 10):`xiaomi-login` 吐登录 URL,agent 转告用户(作为聊天可点链接),用户做完再续跑。
- **redirect_uri 锁死**:`ha_xiaomi_home` 的 OAuth 回调被小米服务端注册死为
  `http://homeassistant.local:8123`,客户端改不了(改 localhost 会 redirect_uri_mismatch)。
- **方案 A(默认,免 sudo)**:小米登录后浏览器 302 到打不开的 `homeassistant.local:8123/api/webhook/
  <id>?code=…&state=…`,但**授权码就在 URL 里,且 HA webhook 按 path 路由、不校验 Host**。
  `xiaomi-callback` 把 host 换成可达的 base_url(localhost)重放 → code 送达 → flow 推进。**不用改 hosts**。
  与 `xiaomi-finish` 共用 `_complete_flow` 轮询。
- **`xiaomi-callback` 的输入健壮性(踩坑修复)**:首选 `--code <值>`——**只传 code**(纯字母数字,无 shell
  特殊字符),webhook path + state 由 CLI 从存好的 `oauth_url` 重建。**不要让 agent 粘整条带 `&` 的 URL**:
  `&` 在 shell 里是后台符,没加引号会把 URL 截断丢掉 `&state=`,导致 state 校验失败、回调不达(agent
  驱动时的头号失败原因,实测遇到过)。位置参 URL 仍保留作 fallback,但要求引号包裹。
- **方案 B(可选)**:`hosts` 把 homeassistant.local 解析到 127.0.0.1,浏览器直接落回 HA,之后走
  `xiaomi-finish`。`hosts` 命令**有 tty(用户在终端跑)就交互 sudo 一步写入**;**无 tty(agent 调)就回退
  打印 sudo 命令**给用户手动跑——**故意用交互 sudo、绝不 `-n`/NOPASSWD**:密码就是用户对这次系统写的授权,
  CLI 不能绕过(铁律 #12)。sudo 密码这关平台无法自动化,是安全底线。
- **每步幂等**:容器已在则跳过 deploy;已 onboarded 则 init 走登录路径;xiaomi_home 已装则跳过。
- **长期令牌 client_name 必须唯一**:HA 拒绝重名,故 mint 时加随机后缀(踩过:重名 mint 失败)。
- **状态串联**:`~/.nn_ha_setup/state.json`(权限 600,存 base_url/access_token/llat/flow_id)让两处
  人工暂停后能续跑。
- **xiaomi config flow 的形状**(v0.4.7 实测):eula → auth_config(区域/语言/redirect)→
  `type=progress` 的 oauth 步,登录 URL 藏在 `description_placeholders` 的 `<a href>` 里
  (故 `_extract_oauth_url` 用 regex 抠);授权后 GET 轮询 flow 直到 create_entry,中间的选择表单
  用 `_submit_form_defaults` 按 schema 默认 + 多选全选自动填。
- **自包含**:只依赖 stdlib + aiohttp(aiohttp 仅用于 mint 长期令牌那一个 WS 调用——HA 无对应 REST)。
  不 import `xyz_agent_context`,因为它作为技能脚本在用户机器上独立运行。

## 上下游

- **上游触发**:`home-assistant-setup/SKILL.md` 指导 agent 顺序调各子命令;`#108` 内置技能机制
  合入后由 `_materialize_builtin_skills` 铺进每个 agent workspace 的 `skills/`。当前分支机制未到,
  interim 由 Smart Home 配置卡的引导 prompt 指路。
- **下游**:`bind` 子命令(或 agent 直接)调 `backend/routes/home_assistant.py` 的
  `PUT /api/home-assistant/binding` 写入 base_url+token → `HomeAssistantModule` 的 MCP 工具即可控制设备。

## 备注

- #108 合入 + rebase 后要补:激活 materialize、run.sh/build-desktop/Dockerfile 三处分发对齐(铁律 #7)、
  bundle 排除测试。见 `reference/self_notebook/todo/2026-07-14-ha-setup-skill-blocked-on-108.md`。
