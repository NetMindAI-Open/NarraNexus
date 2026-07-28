---
code_file: src/xyz_agent_context/integrations/free_tier/wallet_client.py
last_verified: 2026-07-28
stub: false
---

# wallet_client.py — NarraNexus 看待「钱包服务」的全部视角

## 为什么存在

免费额度的钱在 LiteLLM 网关上，而网关的 admin key **只有 deploy 仓的
quota-api 持有**（这正是那个服务存在的理由：后端不该拿到能给任何人开钱包的
凭据）。本文件是这条边界的唯一穿越点：开钱包、查余额，仅此而已。我们从不
知道「虚拟 key」是什么概念 —— 拿到的就是一个不透明的 api_key，和用户自己
粘贴一把 NetMind key 没有区别。

## 为什么放在 agent 包而不是 backend

**两边都要用**：登录路径要开钱包（backend），而 agent 侧的转录 resolver 要
问「这个用户还有没有免费余额」。按铁律 #21 的单向依赖，backend 可以 import
agent 包、反过来不行，所以共用的那块必须落在这里。

## 错误分成三类，是有代价才分的

调用方对三者的反应完全不同，合并成一个异常就等于把「部署配错了」伪装成
「这个用户没有免费额度」：

- `WalletUnavailable` —— 传输层 / 5xx。瞬时，登录路径吞掉，下次登录重试。
- `WalletDenied` —— 401/403。共享 token 配错了，重试没用，必须在日志里刺眼。
- `WalletMissing` —— 余额 404。用户就是还没开钱包（新用户会有几秒处于此态）。

## served_models() 为什么问网关而不是上游

网关只路由（也只知道价格）它被配置的那些模型。上游 NetMind 卖的目录大得多 ——
把上游目录塞进免费卡的下拉框，用户选中一个网关没配的模型，第一次调用就 400；
就算没 400，那个模型也算不出钱。dev 上实测：上游 43 个 vs 网关 15 个。

## 注意

`from_settings()` 未配置时返回 `None` 而不是抛异常 —— 让「这个部署里根本没有
免费额度」在每个调用方都收敛成一句 `is None`。
