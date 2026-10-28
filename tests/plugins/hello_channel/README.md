# Hello Channel

A webhook-transport IM channel installed as a plugin (plugin platform batch 4c): one `ChannelDescriptor`, a `WebhookChannelTriggerBase` trigger, a `ChannelModuleBase` module. Bind through `POST /api/channels/hello_channel/bind`, push events to `POST /api/channels/hello_channel/webhook/{agent_id}` with the issued `webhook_secret`.
