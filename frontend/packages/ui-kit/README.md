# @narranexus/ui-kit

Embed a NarraNexus agent in your own React page:

```tsx
import { ChatWidget } from '@narranexus/ui-kit';

<ChatWidget baseUrl="https://agent.example.com" agentId="agent_x" userId="u1" token={jwt} height="600px" />
```

`ChatClient` is the transport underneath (history over REST, a turn over the `/ws/agent/run` WebSocket) for non-React hosts. Theme with CSS variables (`--nx-bg`, `--nx-fg`, `--nx-user-bg`, `--nx-assistant-bg`, `--nx-border`, `--nx-radius`, `--nx-font`, `--nx-error`). For a framework-free page use `@narranexus/chat-widget` (`<narranexus-chat>`).
