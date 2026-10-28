# Embedding NarraNexus

Three ways to use NarraNexus inside something that is not the NarraNexus app (spec section 19.4).

## Headless engine (Python)

```python
from narranexus.engine import Engine

engine = Engine.load("distributions/minimal")          # or None = every builtin, or a resolved distribution
async for msg in engine.run_turn("agent_x", "u1", "hello"):
    print(msg)
agents = await engine.agents("u1")
engine.events().subscribe("onDidCompleteRun", handler, owner="my.app")
await engine.close()
```

`Engine.load` runs the same boot as the backend host (builtins outside the distribution are dropped, bundled plugins load in stage 1); pass private `Registries()` when embedding several engines or testing. `use_db(client)` runs on a database client you own.

## React component

```bash
npm install @narranexus/ui-kit
```

```tsx
import { ChatWidget } from '@narranexus/ui-kit';

<ChatWidget baseUrl="https://agent.example.com" agentId="agent_x" userId="u1" token={jwt} />
```

`ChatClient` (same package) is the transport for non-React code: `history()` and `send(text, onEvent)` over the app's own REST + WebSocket protocol. On a local deployment omit `token` (the user id is the identity); on cloud pass the NetMind JWT.

## Web component

```html
<script type="module" src="/narranexus-chat.js"></script>
<narranexus-chat base-url="https://agent.example.com" agent-id="agent_x" user-id="u1" token="…"></narranexus-chat>
```

`@narranexus/chat-widget` bundles React and the widget into one file (`npm run build -w @narranexus/chat-widget` → `dist/narranexus-chat.js`).

## Building the packages

The three packages are npm workspaces of `frontend/` (`frontend/packages/{sdk,ui-kit,chat-widget}`); `npm run build:packages` builds them all. `@narranexus/sdk` is what a frontend plugin compiles against (see `getting-started.md`, section 4).
