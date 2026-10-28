---
code_file: src/narranexus/platform/utils/logging/_ship.py
stub: false
last_verified: 2026-09-11
---

## 2026-09-09(B-39)— 缺失 collector 的指数退避 + 云端默认不外发

现象:prod 的 `/telemetry/v1/config` 每天约 3.5k 次 502,95% 来自
prod 自己的容器。两层根因:

- **prod 在未 opt-in 的情况下外发**:#286 把内置默认从 off 翻成
  meta,理由是单租户面有告知横幅 + 设置开关;但多租户云上没有任何
  用户能持有这个开关(PUT 403),prod 栈也没设 `NEXUS_DIAG_SHIP`
  (deploy 仓 diag-collector compose 的注释仍写着"sender default is
  OFF")——于是 prod 每个进程都按 meta 外发,打向一个 prod 从未部署
  的 collector。修:同意链新增第 4 层"云端默认"——`is_cloud_mode()`
  且第 1/3 层都未表态时 `mode=off, source=default`。dev 栈显式
  `NEXUS_DIAG_SHIP=full` 不受影响;manyfold 沙盒走托管默认层不受
  影响;设置页在云上显示开关关闭 + "管理员控制"(managed_by=cloud,
  原有文案)。
- **5xx/网络错误固定 60s 重试**:模拟一天实测每进程 1440 次 GET
  (旧 mirror 立下"5xx 保持 60s 短重试"的规则,对"根本没有
  collector"的部署就是永久 1 次/分钟)。修:`_backoff_delay(base,
  failures)` = `base·2^(n-1)`,封顶 `_BACKOFF_CAP_S`=6h,向下抖动
  25%。5xx/网络从 60s 起步(第一次仍是短重试,瞬态恢复快),3xx/4xx/
  非文档 200 从 TTL 1h 起步;成功解析即清零。同类扫描:熔断冷却也是
  固定 60s(死 ingest = 每分钟一个探测 POST),同样按连续 OPEN 次数
  翻倍,任何 collector 应答(2xx/4xx)清零。
- **探测去重靠锁兑现**(PR#403 review I1):"读 `_discovery_next` +
  判断 + 写 60s 占位"在 `_state_lock` 内原子完成,结果(成功写
  `_resolved_url/_url_expires`、失败 `_discovery_failures += 1` 与
  退避)也在锁内写回;HTTP GET 拆到 `_probe_discovery()`(不碰共享
  状态)在锁外执行,5s 超时不会卡住另一线程。定时线程与 enqueue 线程
  并发时只有一个发 GET。同类:`_dropped_in_cooldown` 的三处累加
  (`__call__`/`_open_breaker`/`flush`)一并收进 `_state_lock`。

测试(`TestMissingCollectorBackoff` / `TestCloudDefault` + 路由
`test_cloud_deployment_without_opt_in_reports_off`):假时钟模拟一天,
502/网络错误 GET ≤20(旧 1440)、404 ≤8(旧 24)、死 ingest POST ≤
阈值+20(旧 1442);成功后下次故障回到短步长;回退 _ship.py 后 8 条红。
`test_concurrent_threads_spend_a_single_probe[502|200]`:6 线程在读
`_discovery_next` 处会合,有锁恰 1 次 GET,去掉锁 6 次(已验证变红)。

## 2026-08-12(rc.2 / #292)— staging 从 dev collector 取发现文档 + 4xx 退避

run.sh 让 **staging 沙盒的发现入口改指 dev-agent**(内置默认仍是
prod)——发现契约从"三边"变**四边**:标签 → 白名单(存储分区)→
map key(接收主机)→ **哪台 collector 伺服这份 map**。四处"只有
prod 一处入口 / URL 轮换=prod 一处 env / 三边契约"的旧表述改口为
四边,共六处:`_ship.py` 模块 docstring(发送端自己的规范性说明)、
collector.py 两处 docstring、`.env.example` 两处、run.sh 一处;外加
两份 mirror 的现状描述段。dev collector
已配 `DIAG_COLLECT_CONFIG_JSON`(curl 实测 200,含 staging→dev-
ingest,贴进 PR)。

`_ingest_url` 加 **4xx→整 TTL 退避**:404(collector 无 CONFIG_JSON)
是明确的"这里没有发现文档",与"200 但非文档形状"同类(3xx 我们
不跟随、自家 collector 直接 200 伺服,故 3xx-4xx 同归 TTL 退避);
**5xx/网络错误保持 60s 短重试**(瞬态,不可扩到它们上——test_5xx_keeps_short_
retry / test_unresolvable_drops_quietly 建立在此);3xx/404/5xx 三条
用例补在 html-backoff 测试旁。run.sh 重定向的可执行不变量测试落在
**tests/utils/logging/test_diag_ship.py**(遥测契约测试聚拢处;复用
run.sh 文本断言的做法,用本地 `parents[3]` 读取仓库根)——断言方式
是**从重定向 export 行反向锚定它所在 if 的条件**,要求同一条件里
重定向、`"staging"` 标签、`_is_manyfold_sandbox` 门控三者共现
(整文件 `in text` 会因 token 在别处出现而虚假通过)。重定向门控加
`_is_manyfold_sandbox`——个人装机手设 staging 标签不会把日志泄漏到
dev。

## 2026-08-12(四审)— 同意标记的持久性是挂载属性

四审抓住托管默认层正当性链条的最后一环:"opt-out 仍然赢"要求
**标记活得过沙盒重建**,而默认落点 `$HOME/.narranexus/…` 在容器里
是可写层(Dockerfile.manyfold 的持久面是 /data)——升级/重建即丢,
full 档静默复发。修:run.sh 容器模式把
`NEXUS_DIAG_OPTOUT_FILE` 默认指到 **/data/telemetry_optout**(与
workspaces/logs/db 同一持久卷:用户的 opt-out 与其数据同寿)。
docstring 第 2 层补"耐久性是挂载属性"——**改 Dockerfile 的
HOME/卷布局就是在改同意状态**,该句是给下一个动镜像布局的人的。
bodyManaged 文案同批补 full 档内容句(managed 变体恰恰多为 full
部署)。ops 排序备忘:rc.2 沙盒铺开前,线上两台 collector 的
KNOWN_ENVS/discovery 需先含 sprite(记 todo)。

## 2026-08-11(十一)— 三审 ⛔:沙盒 full 从"覆盖"降到"托管默认层"

初版 run.sh 给沙盒 `export NEXUS_DIAG_SHIP=full`,占的是同意链第 1
层——三件事同时成立:发的是含完整用户消息的 full、沙盒用户开关灰
+PUT 409(撤回权为零,本 PR 建的"撤回一个 flush 生效"在沙盒上不
存在)、managed 文案比实际外发窄。修复(评审推荐方案①):

- **同意链插入第 3 层"托管默认"**:`env > optout >
  NEXUS_DIAG_DEFAULT_SHIP(meta|full)> 内置 meta`。它改变的是"用户
  未表态时适用什么",**用户的 optout 标记仍然赢、开关仍然活**;
  `off` 不被接受(想静默是覆盖层的事,"默认关"会渲染一个开不了的
  开关)。run.sh 改为导出 `NEXUS_DIAG_DEFAULT_SHIP=full`;
- **沙盒身份判据对齐单一解析点**(manyfold_outbound.
  manyfold_runtime_env:token+runtime_id 双非空,webhook URL 明文
  允许缺席):原 URL 嗅探会漏掉 escape-hatch 配置的真沙盒,更糟的
  是让仅设了 URL 的自托管容器开始外发用户正文;
- **非 staging 沙盒获得专属标签 `sprite`**(三边同批:KNOWN_ENVS +
  discovery 示例):否则 prod 沙盒回落 "cloud" 与 prod 栈同分区,
  full 量级(比 meta 高 1-2 个数量级)会按最旧优先冲掉 prod 诊断
  历史;429 在发送端走 4xx 丢弃不重试,预算打满伤及全体;
- 告知文案点名 full 档内容("整行外发,可能含消息内容"),
  desc 与横幅 body 同批改口,10 语种。

## 2026-08-11(十)— 同意 UI PR:默认 off→meta,撤回即时生效

`_DEFAULT_MODE` 翻为 **meta**(非 full)——与它的同意基础(首次告知
横幅 + 设置→隐私开关)同一变更落地,兑现第 3 轮立下的"默认值与
同意基础同批到达"。**为什么是 meta 不是 full**(预审 Critical):
full 逐字外发 INFO 行,而生产 INFO 含完整用户消息
(agent_runtime 的 `Input content:`)、IM 入站正文、LLM 结构化输出
——告知文案("启动、错误、诊断事件")没有覆盖这些;`redact()` 在
ship 路径上也从未被调用。full 到达一台机器只有两条路,都是明确的
部署行为:**托管默认层**(见三审条目——我方 run.sh 给 manyfold
沙盒注入 `NEXUS_DIAG_DEFAULT_SHIP=full`,用户 opt-out 仍然赢)或
机器本地 `.env` 的显式 `NEXUS_DIAG_SHIP` 覆盖(仓库不断言哪台机器
这么配了);把**内置默认**抬到 full 的前置是 ship 侧脱敏 + 重写
告知文案。配套:

- **发现探测退避**:2xx 但非发现文档形状(如 SPA fallback 的
  200+HTML)按"本部署没有遥测服务"处理,退避整个 TTL(1h)而非
  60s 网络重试——闲置安装群不得每分钟向 vendor 发信标;网络错误
  仍走短重试(网络坏是瞬态,端点错不是);
- 时效措辞修正:重启只在"启动时遥测本就关闭"时才需要;运行中
  off→on 经同一 `_send` 闸门下个 flush 即恢复(本 PR 自己的测试
  证明了这点,文档曾与其矛盾);
- 标记文件作用域如实改口:per-USER-ACCOUNT per-host(Path.home()),
  非严格 per-machine——不同 HOME 的旁进程各持其态,桌面装机所有
  sidecar 共享 HOME 时该区别潜伏;
- **标记路径可配置**(`NEXUS_DIAG_OPTOUT_FILE`,二审补):容器化
  单租户自托管(SQLite+compose,cloud 判定走 local、PUT 放行)下
  `~/.narranexus` 不是卷——backend 容器写的 marker 其他容器看不见
  (它们继续外发而 UI 报"已关闭"),且随可写层在 recreate 时消失。
  补一个共享挂载点的路径旋钮;我方 compose 栈的跟进(挂卷 + 设变量)
  记在 deploy 侧待办。

原"三件配套"如下:

- **公开同意 API**:`telemetry_consent()`(mode + 决定它的层级
  env/optout/default——UI 只在 optout/default 时提供开关)与
  `set_telemetry_optout()`,经包 `__init__` **惰性**再导出
  (_ship 顶层 import httpx,import 期失败只能坏 shipping 不能坏
  logging 包);`ship_mode()` 降为 `telemetry_consent()` 的视图,
  优先级规则单点;
- **撤回不等重启**:`_send`(唯一出口,flush 与 backfill 共用)每次
  外发前重查 `ship_mode()`,optout 写入后一个 flush 周期内静默;
  重新开启等下次启动(sink 未注册)——不对称性偏向隐私一侧;
- **per-machine 语义由调用方把关**:标记文件是整机的,backend 路由
  在多租户云模式拒写(403)、env 覆盖时拒写(409)。

## 2026-08-11(九)— 第 10 轮:路由测试的诚实边界

路由测试守的是解析规则(注入 map)+ .env.example 示例内容(新增
对示例 ingest key 集的断言);线上 DIAG_COLLECT_CONFIG_JSON 是 ops
配置,代码不可达——"测试守住第三边"按此收窄口径。三个路由用例
parametrize 合并;docstring 折行分段;import 位置进 first-party 组。

## 2026-08-11(八)— 第 9 轮:契约实为三边

第 8 轮的 BILATERAL 口径漏了后果最重的一边:白名单只决定目录,
**discovery map 的 key 才决定送到哪台机器**,缺 key 的标签静默回落
"default"(prod)。docstring 与 .env.example 全部改口为三边契约,
路由测试覆盖 "dev" 键的解析路径(诚实边界见第 10 轮);import
位置归位。

## 2026-08-11(七)— 第 8 轮:契约写全两边

模块 docstring 的 URL 解析段补**双边契约**声明(自定义
`NEXUS_DIAG_ENV` 必须同时进收集端 `DIAG_COLLECT_KNOWN_ENVS`)与
标签链第三级的真实语义(部署模式兜底第二档是 DB-URL 猜测——
best-effort 路由,不是强身份);`get_deployment_mode` import 挪到
模块顶。dev 云栈标签决策(方案 A,`NEXUS_DIAG_ENV=dev`)见
collector 镜像第 8 轮条目。

## 2026-08-11(六)— 第 7 轮:标签兜底改读部署契约

`_env_label()` 第三级回落从字面量 "unknown" 改为
`get_deployment_mode()`(纯 env 叶子模块):云栈容器不经 run.sh,
`NARRA_SURFACE` 没人注入,原实现让**所有云上记录**自标 unknown、
落进收集端最先排干的陌生桶。"不嗅探别家集成的 env"(manyfold)与
"读自家部署契约"(`NARRANEXUS_DEPLOYMENT_MODE`,云镜像已烘焙)是
两回事。桌面侧同批修:process_manager.rs 给全部 sidecar 注入
`NARRA_SURFACE=desktop`(原来只有 backend)。

## 2026-08-11(四)— 第 5 轮:atexit 顺序

(第 6/7 轮备注:`logger.complete()` 使"每个进程退出时排空 loguru
enqueue 队列"成为**全局**退出期行为——这是**已知且被接受的退出期
代价**:默认 off 时它只是顺带排空文件 sink 队列(无害略有益);
sink 开启时退出期最多多等一次队列排空 + flush 超时。接受理由:不
排空则最后一批必然丢,而"崩溃尾巴靠下次启动 backfill 找回"只覆盖
文件本源,不覆盖立即外发。)

atexit 是 LIFO,本模块 handler 在 import 时注册——晚于 loguru 自身
的 teardown 注册,因此**先于** loguru 排空 enqueue 队列执行;直接
flush+close 意味着队列尾部的记录在 close 之后才进 sink 缓冲,最后
一批**必然**发不出。修法:handler 内先 `logger.complete()`(等队列
排空且不拆 handler——review 建议的 `remove()` 会连文件 sink 一起
拆掉,complete 语义更准)→ flush → close。conftest 的灭火开关同批
改为**无条件**赋值(setdefault 挡不住开发者 shell 里的
`export NEXUS_DIAG_SHIP=full`)并修正过期注释。

## 2026-08-11(三)— 第 4 轮收口

Tier-1 docstring 的 URL 解析段同步删除 manyfold 嗅探描述(镜像与
docstring 曾互相矛盾);`_setup` 的 `from ._ship import` 移入 try
("shipping is optional, logging is not" 必须覆盖 import 期失败);
发现文档防御性解析(非 `{"ingest": {...}}` 形状按无效处理,收集端
`/v1/config` 同步做形状校验以免发送端静默不解析);atexit 扫尾补
`client.close()`。

## 2026-08-11(二)— 安全轮:默认 off 合入 + 发现 URL 白名单 + 并发/字节修正

- **默认改回 off**(`_DEFAULT_MODE`):review Critical——"默认开"所
  依赖的同意基础(首次告知 UI + optout 写入)不在本 PR;默认值与其
  同意基础必须**同批到达**,UI PR 落地时再翻(当时预期 full,实际
  落地为 meta,见第十条)。顺带消解"合并即
  dev 环境未经 ops 同意定时打 prod 域名";
- **发现 URL 白名单**:`https` + `*.narra.nexus` 才接受——发现文档
  来自公开端点且决定用户日志去向,被劫持/配错时拒绝切换、保留旧值;
- **长生命周期 httpx.Client**(发现与发送共用):每请求新建客户端 =
  一次投递吃两回 TCP+TLS 冷握手、各自只有 2s——健康但握手慢的
  收集器会被误判成 5 连超时而熔断;
- manyfold env 嗅探移出(通用工具不读别家集成的 env)——staging
  判定移到 run.sh 容器模式,派生注入 `NEXUS_DIAG_ENV`;
- 缓冲改存 **encoded bytes**(len(str) 数字符,中文正文约 12MB 才触
  "4MB"阈值);熔断状态加 `_state_lock`(RLock);4xx 应答同时清
  半开(收集器活着即视为探测成功,否则半开态粘住)。

## 2026-08-11 — 遥测化重构 v2(与 manyfold 负责人对齐后)

从"运维特性(平台注入 env)"改为**产品遥测(用户同意)**,对
manyfold 的观测性依赖清零,且顺带覆盖本地 DMG 用户:

- **同意模型**:env 显式覆盖(off/meta/full,也是测试套件灭火开关,
  见 tests/conftest.py)> `~/.narranexus/telemetry_optout` 标记文件
  (设置 UI 写)> 缺省开(首次告知在 UI;当时预期 full,同意 UI
  落地时定为 **meta**,理由见第十条);
- **URL 发现**(rc.2 起为多入口):代码写死内置默认入口
  `https://agent.narra.nexus/telemetry/v1/config`(**staging 沙盒经
  run.sh 改指 dev-agent**,故 dev collector 也须自带发现文档;prod collector 的
  `/v1/config` 伺服,env 可覆盖),响应是 env→ingest URL 的 map,
  发送端按自身标签选路(显式 `NEXUS_DIAG_ENV` > manyfold webhook
  含 api-staging 判 staging > NARRA_SURFACE)——staging 噪声进
  dev-agent.narra.nexus,URL 轮换零发版。**惰性解析**:只在 worker
  线程首发时拉取,TTL 1h、失败退避 60s、stale-if-error——进程启动
  与测试永不碰网;解析不出 = 静默丢批(stderr 一次),不入熔断账;
- **去 token**:仓库开源,任何内置/共享 secret 都等于公开——收集端
  转公开形态,防线是滥用控制;复用 manyfold token 被否(受众错位、
  我方无法校验、本地面根本没有)。`NEXUS_DIAG_SHIP_TOKEN` 留作私有
  收集端旋钮。

# _ship.py — 网络日志 sink(观测性 push 半边)

## 为什么存在

部署方 env 决定外发级别(`NEXUS_DIAG_SHIP=off|meta|full` +
URL/TOKEN;env 旋钮清单见 `.env.example`),`setup_logging` 末位注册;
文件 sink 永远是本源,这里只运副本到收集器
([[../../../../scripts/diag_collector/collector.py|collector]])。
pull 半边 = manyfold 诊断端点,两者刻意零共享机制。

## 设计决策

- **不干扰被观测进程是最高约束**:`enqueue=True` 独享队列线程,业务
  协程零等待;慢收集器只堵自己的 worker,不堵文件 sink(各自队列);
  发送失败**丢批**(文件本源在,缺口走诊断 pull),失败经 `sys.stderr`
  报告——坏掉的 ship 绝不能经 loguru 发声(自递归);
- **级别在 loguru 分发层执行**:meta = 注册时 level=25(AUDIT 及以上,
  含审计镜像行与告警);full = **跟随文件 sink 的 resolved_level**——
  排障开 DEBUG 时远端必须同步可见,硬编码 INFO 恰好在最需要的场景
  失效(review 修);被滤记录零成本;
- **熔断**(review 两轮):连续 5 次**瞬态**失败 OPEN 60s(连续重开
  按指数翻倍至 6h 封顶,见 B-39 条目)——期间
  记录在门口直接丢、不入缓冲(否则死收集器 = 无界 enqueue 队列反噬
  被观测进程内存)。冷却后 **HALF-OPEN 真实现**(第二轮修:此前只
  存在于注释——探测批失败**立即**重开,不再重攒 5 次,消灭每 70s
  白烧 10s 的稳态;成功则全闭)。**4xx 永久拒绝不入熔断账**:413 是
  "这批不合格"不是"收集器死了",丢批+单独告警;
- **按字节分批**(第二轮修):序列化长度 ≥4MB 提前 flush(收集端
  线上 8MB 封顶的一半,留 gzip 余量)——纯条数分批可能撞上按字节
  封顶的接收端,批批 413;缓冲改存序列化行,字节计数零额外开销;
- **atexit flush**(review 两轮):**单个模块级 handler + WeakSet**
  ——per-instance 注册会把每个构造过的 sink 钉在 atexit 表里(多
  service 进程与测试线性累积,退出时串行叠 2s 超时);熔断开着时
  flush 直接丢弃不发,不对已知死收集器烧超时;
- **身份两层**:每条记录平铺信封字段(env/runtime_id/host/service +
  run_id/event_id extras);sprite 单 runtime,信封即用户归属;
- **崩溃尾巴补发**:注册时把当日既有文件末 200 行按 `backfill: true`
  发一次——重复行收集端可容(读时去重),丢崩溃现场不可容;
- 批:200 条或 3s;gzip;2s 超时;失败第 1 次与每 50 次提示一次。

## Gotcha

- 云端 executor 进程不走 `setup_logging`(裸 logger.add)→ 暂不入
  sink,决策 1 布局统一时一并收(todo §C 已记);
- 定时 flush 是 daemon 线程,不阻塞解释器退出——代价是进程秒退时
  最后 <3s 的批靠下次启动的 backfill 找回。
