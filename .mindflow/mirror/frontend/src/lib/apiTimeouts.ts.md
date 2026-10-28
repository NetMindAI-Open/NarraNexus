---
code_file: frontend/src/lib/apiTimeouts.ts
last_verified: 2026-09-04
stub: false
---

# apiTimeouts.ts — 请求超时常量的家

## 为什么单独一个文件

[[api.ts]] 把它放到 fetch 的 `AbortSignal.timeout` 上，[[../hooks/useStudioTurn.ts]] 要用同一个
数字派生自己的等待上限。但测试是整体 `vi.mock('@/lib/api')` 的（那是一个大 class），named
常量若住在 api.ts 里，每个这样的 mock 都缺它、hook 一 import 就炸。放在这里既能被 hook 直接
import，数字仍只有一份。

`PATROL_WRITE_TIMEOUT_MS` 仍在 api.ts（只有 api.ts 自己用，没有跨模块需求），不为整齐而搬。
