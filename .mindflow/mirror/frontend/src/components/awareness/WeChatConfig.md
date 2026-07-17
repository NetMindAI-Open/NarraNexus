---
code_file: frontend/src/components/awareness/WeChatConfig.tsx
stub: false
last_verified: 2026-07-13
---

## 2026-07-13 — activation toggle + parent-list sync

Renders the shared `ChannelActiveToggle` in the bound state (flip `enabled` via `POST /api/wechat/set-active`) — primary use is activating a bundle-imported (inactive) credential. The toggle handler AND the header refresh button now call `onBindStateChange` so the parent `IMChannelsSection` status badge updates immediately (was stale until remount).

## Why it exists

Per-agent WeChat binding UI inside the Awareness panel's IM Channels
section. Registered in ``IMChannelsSection``'s ``IM_CHANNELS`` like
Lark / Slack / Telegram, same Card shape and ``onBindStateChange``
contract.

The deliberate contrast vs. every bot channel: there is **no token to
paste**. Personal WeChat authenticates by scanning a login QR with the
phone, exactly like WeChat web. So this card's bind flow is a QR-scan
loop, not a form submit — a genuinely different UX shape that the
generic IM-channel framing has to accommodate.

## Design decisions

- **Three render states, not two.**
  1. **No account bound** — explainer + "Connect WeChat" + a yellow
     caution that this signs in a *personal* account via a third-party
     gateway (outside WeChat's official Bot terms).
  2. **QR shown, waiting for scan** (``polling``) — renders the QR and
     a "Waiting for scan…" spinner with a Cancel button.
  3. **Account bound** — connected status + Disconnect.
- **Bind = two server calls.** ``handleConnect`` calls
  ``api.startWeChatQrcode`` to get ``{qrcode, qr_url}``, shows the QR,
  then fires a recursive poll loop on ``api.pollWeChatQrcode``. On
  ``status:"confirmed"`` it stops polling and **re-fetches the
  sanitised credential** (the token is never returned to the
  frontend — the backend persisted it at confirm time).
- **The poll loop is paced by the server, not the client.**
  ``/qrcode/poll`` long-polls on the gateway side, so the loop
  re-invokes immediately on ``"wait"`` — there is no client-side
  ``setTimeout``/tight-loop. The server call IS the pacing. Documented
  inline so nobody "fixes" it by adding a delay.
- **``pollingActiveRef`` guards the recursive loop.** A plain ``ref``
  flag (not state) gates every iteration; flipping it to ``false``
  (Cancel, unmount, or agent change) stops the loop cleanly. The loop
  is fired as ``void runPollLoop(...)`` — fire-and-forget but
  self-guarding, with try/catch around each gateway call so a thrown
  poll doesn't become an unhandled rejection.
- **QR generated client-side from ``qr_url`` (``<QRCodeSVG>``), not
  ``<img src>``.** Verified live: the gateway's ``qr_url`` is a WeChat
  short URL (``https://liteapp.weixin.qq.com/q/<code>?qrcode=...&bot_type=3``),
  NOT an image — an ``<img src={qr_url}>`` just 404s. We encode that URL
  into a QR with ``qrcode.react`` (``QRCodeSVG value={qr_url}``, white
  quiet-zone padding) so it renders inline in the panel and the phone
  scans it directly. A secondary "Can't scan? open the QR page" ``<a>``
  to ``qr_url`` stays as a fallback — that liteapp page renders its own
  QR (``X-Frame-Options: SAMEORIGIN`` so it can't be iframed inline).
- **"Owner pending" note in the bound state.** The owner's wxid is
  opaque until the first inbound DM (the gateway never reveals it at
  bind time), so when ``credential.owner_wx_id`` is empty the card
  shows a yellow "Owner registration pending — message this account
  once" note. Same first-contact trust-signal idea as Telegram's
  @username, adapted to WeChat's reveal-on-DM constraint.
- **``mountedRef`` guards every async setState** and
  ``pollingActiveRef`` is cleared on unmount — prevents the classic
  React "setState after unmount" warning and orphaned poll loops.
- **``useConfirm`` modal on Disconnect.** Destructive — shared
  confirmation pattern across the IM channel components.

## Upstream / downstream

- **Composed by**: ``IMChannelsSection.tsx`` (registered in
  ``IM_CHANNELS`` with the ``QrCode`` icon).
- **Calls**: ``api.startWeChatQrcode / pollWeChatQrcode /
  getWeChatCredential / unbindWeChat``.
- **Reads**: ``useConfigStore().agentId``.
- **Types**: ``WeChatCredentialData`` from ``@/types``.

## Gotchas

- The poll loop must never be turned into a client-timer loop — the
  long-poll on the server side is the rate limiter. A client delay
  would just add latency to the confirm.
- ``pollingActiveRef`` is the single source of truth for "is the loop
  alive". Anything that should stop polling (cancel / unmount / agent
  switch) must flip it via ``stopPolling`` — don't rely on the
  ``polling`` state alone, which is for rendering.
- The bound state keys "connected" off ``credential`` truthiness; the
  "owner pending" sub-state keys off ``owner_wx_id`` being empty.
  Don't conflate the two — a freshly bound account is connected AND
  owner-pending until the first DM.
- The yellow "personal account / third-party gateway" caution is a
  deliberate honesty signal, not boilerplate. Keep it: personal-WeChat
  automation carries account-risk the user should see before binding.
