---
code_file: frontend/src/components/awareness/HomeAssistantConfig.tsx
last_verified: 2026-07-17
stub: false
---

# HomeAssistantConfig.tsx — Smart Home 绑定配置卡

## 为什么存在

右侧 Smart Home tab 里的配置卡:把某个 agent 连到用户的 Home Assistant(base_url +
Long-Lived Access Token),之后该 agent 就能通过 HomeAssistantModule 查/控设备。**per-agent**
(不是全局)——用户有多套 HA 时,不同 agent 可绑不同 HA。和 IM 渠道并列但独立,不是渠道。

## 关键点

- 依赖 `useConfigStore().agentId` 定位当前 agent;调 `api.getHABinding/saveHABinding/testHAConnection/
  verifyHABinding`。
- **两种"测试"要分清**:`Test`(编辑态)测**表单里现填**的 URL+token;`Verify`(已绑定态)测**库里已存**
  的绑定(走后端 `/verify` → `resolve_client`,即 agent 实际读取路径,绿了才真证明 agent 能连)。已绑定态
  拿不到明文 token(只回显掩码),所以必须有 Verify 这条"用存量凭据"的验证。
- 未绑定态附带一段**可复制的 onboarding prompt**(`awareness.homeAssistant.setupPrompt`):给还没有 HA
  的用户,复制发给 agent 让它跑 `home-assistant-setup` 技能自动搭。
- token 是敏感凭证:GET 只回显掩码(末 4 位),明文只在用户新填/重绑时经 PUT 上行。

## 上下游

- **被谁用**:`AwarenessPanel`(`section==='smarthome'`)与右侧书签 `smarthome` tab 渲染它。
- **依赖**:`@/lib/api` 的 HA 方法(→ `backend/routes/home_assistant.py`)、`useConfigStore`、
  `awareness.homeAssistant.*` i18n。
- 后端绑定按 `agent_id` 存(`instance_homeassistant_bindings`),route 侧有 agent 归属校验。
