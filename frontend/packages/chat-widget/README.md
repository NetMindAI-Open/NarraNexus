# @narranexus/chat-widget

```html
<script type="module" src="https://cdn.example.com/narranexus-chat.js"></script>
<narranexus-chat base-url="https://agent.example.com" agent-id="agent_x" user-id="u1" token="…" height="600px"></narranexus-chat>
```

Attributes: `base-url`, `agent-id` (required), `user-id` (required), `token` (cloud JWT; omit on local deployments), `height`, `placeholder`, `history-limit`. Attributes are live. The bundle contains React and `@narranexus/ui-kit`; nothing else is needed on the page.
