---
code_file: src/xyz_agent_context/agent_framework/providers/driver/drivers/netmind_free.py
last_verified: 2026-07-28
stub: false
---

# netmind_free.py — 免费额度卡的 Driver

## 为什么是一个空壳子类

因为它**应该**是空的。免费额度卡就是一张 NetMind 卡，只不过钥匙是我们网关上的
钱包而不是用户自己的 NetMind 账号 —— 两行结构、协议、auth_type、聚合器限制全部
一致。所以这里只换了一个注册键。

反过来说：哪天这个类里开始出现自己的 config 构建逻辑，就说明「免费额度是一张
普通 NetMind 卡」这个论断已经不成立了 —— 那是个该停下来重新想的信号，不是该
继续往下写的地方。

## 为什么用继承而不是把 NetMindDriver 注册两次

继承把「它是一张 NetMind 卡」这件事写在类型里，而且以后 NetMind 侧长出任何新
行为，这边自动跟上。

## 这个文件是怎么被发现缺失的

dev 上真机跑一轮对话时报的：
`cannot determine driver_type from (source='netmind_free', ...)`。
resolver 接受了这张卡、slot 也绑上了，但没有 Driver 能把它变成运行配置 ——
一个「看起来完全正常的配置」在运行时 500。

`test_every_dual_card_type_has_a_driver` 就是为这一类遗漏加的门禁：任何双行卡型
都必须能推导出 driver_type 且该 driver_type 已注册。
