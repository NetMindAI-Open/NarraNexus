/**
 * @file_name: registry.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The one registry shape every frontend contribution point uses.
 *
 * Mirrors the kernel's Python `Registry[T]`: entries keep registration order
 * (builtin shell entries first, plugins after), a duplicate id is an error
 * unless `replace` is passed, `register` returns a disposer, and `freeze`
 * ends registration. Consumers read through `list()` / `get()`; nothing
 * iterates a hardcoded array any more.
 *
 * A disabled owner's writes are rejected here (not just purged once at
 * disable time): several shell contribution points (`ui.channels`,
 * `ui.settingsSections`) register lazily, the first time their surface is
 * opened, which can be well after `disableBuiltinUi(owner)` ran at boot. A
 * one-shot `removeOwner` sweep at disable time cannot see a registration
 * that has not happened yet, so the blacklist has to live where every
 * `register()` call passes through — this class — not in the loader.
 */

export interface RegistryEntry<T> {
  id: string;
  value: T;
  owner: string;
}

const disabledOwners = new Set<string>();

/** Mark `owner`'s future registrations as no-ops across every registry. Existing entries are untouched — call `removeOwner` on each registry to purge those. */
export function disableOwner(owner: string): void {
  disabledOwners.add(owner);
}

/** Test/dev hook: allow `owner` to register again. */
export function enableOwner(owner: string): void {
  disabledOwners.delete(owner);
}

export function isOwnerDisabled(owner: string): boolean {
  return disabledOwners.has(owner);
}

export class RegistryConflictError extends Error {
  constructor(kind: string, id: string, owner: string) {
    super(`${kind}: "${id}" is already registered by "${owner}"`);
    this.name = 'RegistryConflictError';
  }
}

export interface RegisterOptions {
  owner?: string;
  replace?: boolean;
}

export class Registry<T> {
  readonly kind: string;
  // ECMAScript `#`-private fields, not TypeScript `private` — a `private` field makes the
  // class's structural type include those members, and once `REGISTRIES` (an object of
  // `Registry<T>` instances for 17 different `T`s) is exported as a value from
  // `frontend/src/platform/registries/index.ts`, `@narranexus/sdk`'s declaration build (which
  // transitively re-exports `HostAPI`, whose `registries` field is typed off `typeof
  // REGISTRIES`) has to print that structural type into the emitted `.d.ts` — and TS4094
  // ("... of exported anonymous class type may not be private or protected") fires because a
  // `.d.ts` cannot re-declare a `private`/`protected` class member outside its own module. True
  // `#`-private fields are invisible to structural typing entirely, so they carry no such
  // restriction.
  readonly #entries = new Map<string, RegistryEntry<T>>();
  #frozen = false;
  readonly #listeners = new Set<() => void>();
  #cached: RegistryEntry<T>[] | null = null;
  readonly #validate?: (value: T) => void;

  /**
   * `options.validate` (M-11) is the ONE way to build a "validating registry" — a registry that
   * rejects a malformed value at registration time instead of accepting it and failing later at
   * render/evaluation time. It replaces two previously-coexisting, functionally-identical
   * patterns: `themes.ts` subclassed `Registry` and overrode `register`; `slotPoints.ts`'s
   * `validated()` helper overwrote the instance's own `register` property. Both are now this.
   */
  constructor(kind: string, options: { validate?: (value: T) => void } = {}) {
    this.kind = kind;
    this.#validate = options.validate;
  }

  register(id: string, value: T, options: RegisterOptions = {}): () => void {
    this.#validate?.(value);
    if (this.#frozen) throw new Error(`${this.kind}: registration is closed`);
    const owner = options.owner ?? 'builtin.ui';
    // A disabled builtin/plugin never gets a foothold, no matter when it tries to register.
    if (isOwnerDisabled(owner)) return () => {};
    const existing = this.#entries.get(id);
    if (existing && !options.replace) throw new RegistryConflictError(this.kind, id, existing.owner);
    const entry: RegistryEntry<T> = { id, value, owner };
    this.#entries.set(id, entry);
    this.#notify();
    return () => {
      if (this.#entries.get(id) === entry) {
        this.#entries.delete(id);
        this.#notify();
      }
    };
  }

  get(id: string): T | undefined {
    return this.#entries.get(id)?.value;
  }

  /** Same as `get`, but throws instead of returning `undefined` — for call sites where a
   *  missing entry is a programming error (a slot's own gate id must always resolve), not a
   *  legitimately-absent contribution. Mirrors the kernel's `Registry.get` `UnknownEntry` raise. */
  getOrThrow(id: string): T {
    const value = this.get(id);
    if (value === undefined) throw new Error(`${this.kind}: unknown entry "${id}"`);
    return value;
  }

  has(id: string): boolean {
    return this.#entries.has(id);
  }

  /** The owner that registered `id`, or `undefined` if it is not registered. */
  ownerOf(id: string): string | undefined {
    return this.#entries.get(id)?.owner;
  }

  list(): RegistryEntry<T>[] {
    return [...this.#entries.values()];
  }

  /** Same content as `list()` but referentially stable until the next change. */
  snapshot(): RegistryEntry<T>[] {
    if (this.#cached === null) this.#cached = this.list();
    return this.#cached;
  }

  ids(): string[] {
    return [...this.#entries.keys()];
  }

  /** Drop every entry registered by `owner` (a disabled builtin's whole UI row). Returns the removed ids. */
  removeOwner(owner: string): string[] {
    const removed: string[] = [];
    for (const [id, entry] of this.#entries) {
      if (entry.owner === owner) {
        this.#entries.delete(id);
        removed.push(id);
      }
    }
    if (removed.length) this.#notify();
    return removed;
  }

  freeze(): void {
    this.#frozen = true;
  }

  /** Subscribe to changes (plugins register after first render). */
  subscribe(listener: () => void): () => void {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  }

  #notify(): void {
    this.#cached = null;
    for (const l of this.#listeners) l();
  }
}
