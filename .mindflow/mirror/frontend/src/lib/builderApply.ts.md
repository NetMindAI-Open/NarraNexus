---
code_file: frontend/src/lib/builderApply.ts
last_verified: 2026-09-03
stub: false
---

# builderApply.ts — 把 draft 变成 agent 上的真实改动

## 那条刻意的分界

```
名称 / 描述 / 指令   → 实时写入，不需要确认
Skills / Channel     → 只做推荐，由用户点击落地
```

文本字段可以直接写，因为它们**便宜且可见**：面板就摆在那里，用户随手就能改回
来；而且「让对话把面板填好」本来就是这条路径存在的理由。

Skills 和 Channel 不行。安装 skill 会把文件复制进 agent 的 workspace，模型中途
改主意就会在用户眼前**装了又卸**。绑定渠道需要凭证，那是用户的东西，绝不能到
模型手上。所以草稿对这两项只**推荐**，落地要人点。

## 为什么先 diff

未变化的字段**不发请求**。否则每一条回复都会把同样的指令 PUT 回去，agent 的
更新时间戳会在什么都没改的轮次上抖动。

## 为什么错误只收集不抛

一次写失败在面板里呈现（经 [[../stores/studioStore.ts]] 的 `applyError`，
[[../hooks/useStudioTurn.ts]] 写、[[../components/builder/BuilderConfigPanel.tsx]] 读），但**不能打断用户正在进行的对话**（铁律 #15 —— 平台不
许成为中断源）。读 awareness 失败也同理：降级成空串告诉模型「还没有指令」是可
恢复的，卡住发送不是。

`readCurrentConfig` 的身份字段由调用方传入而不是再查一次接口 —— 它每轮都跑，
一次可避免的往返累积起来很可观。
