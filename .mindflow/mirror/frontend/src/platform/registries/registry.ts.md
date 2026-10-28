---
code_file: frontend/src/platform/registries/registry.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 注释：`REGISTRIES` 现有 17 种 `T`

无行为变化。

## 2026-09-07 — I-2 disabled-owner blacklist + getOrThrow + #private fields (not `private`)

`register()` now rejects (returns a no-op disposer) writes from any owner in the module-level
`disabledOwners` set (`disableOwner`/`enableOwner`/`isOwnerDisabled`), so a builtin disabled at
boot stays gone even if its contribution point (e.g. `ui.channels`) registers lazily, well after
the disable call ran — a one-shot `removeOwner` sweep at disable time cannot see a registration
that has not happened yet. Added `getOrThrow(id)` (mirrors the kernel's `Registry.get`
`UnknownEntry` raise) for call sites where a missing entry is a programming error, and
`ownerOf(id)`.

All instance fields (`entries`, `frozen`, `listeners`, `cached`) and the `notify` method were
converted from TypeScript `private` to ECMAScript `#`-private. Reason: once `REGISTRIES` (16
`Registry<T>` instances, see `registries/index.ts`) is exported as a value and `@narranexus/sdk`'s
declaration build (`packages/sdk/tsconfig.build.json`, `declaration: true`) transitively reaches
it (via `HostAPI.registries`, typed off `typeof REGISTRIES`, re-exported from `packages/sdk/src/types.ts`),
TypeScript has to print `Registry<T>`'s structural type into the emitted `.d.ts` — and a
`private`/`protected` member cannot be re-declared outside its own module, so `tsc -p
tsconfig.build.json --noEmit` failed with TS4094 ("... of exported anonymous class type may not
be private or protected") on every private field. `#`-private fields are invisible to structural
typing and carry no such restriction; the app's own `tsc -p tsconfig.app.json` build (no
`declaration`) was never affected by this — only the SDK package's own type-emit build was.

## 2026-09-03 — 前端注册表的唯一形状（对应内核 `Registry[T]`）

插件平台批 1。与 Python 侧同语义：注册顺序即列表顺序（壳先、插件后）、重名默认报错
（`RegistryConflictError`）除非 `replace`、`register` 返回撤销函数、`freeze` 关闭注册。
额外两样是 React 需要的：`subscribe` 让组件在插件晚于首帧注册时重渲染，`snapshot()` 在两次变更之间
返回同一引用（`useSyncExternalStore` 的要求，否则无限重渲染）。`subscribe` 的退订函数返回 void
（不泄漏 `Set.delete` 的布尔值到调用方签名）。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

`removeOwner(owner)` drops a whole owner's row and notifies once; the loader uses it to make a disabled builtin's pages/sidebar/panels/commands disappear at boot.

## 2026-09-07 — `validate` constructor option unifies the two "validating registry" patterns (M-11)

Two functionally-identical but differently-implemented "validating registry" patterns coexisted:
`themes.ts` subclassed `Registry` and overrode `register`; `slotPoints.ts`'s `validated()` helper
overwrote the instance's OWN `register` property. `Registry`'s constructor now takes an optional
`{ validate?: (value: T) => void }`; `register()` calls it (if present) before the frozen/owner/
duplicate checks — a throw rejects the registration. Both call sites now use this one mechanism;
neither subclasses `Registry` nor monkey-patches an instance method anymore.
