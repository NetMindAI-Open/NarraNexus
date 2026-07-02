---
code_file: src/xyz_agent_context/module/wechat_module/_wechat_credential_manager.py
stub: false
last_verified: 2026-06-24
---

## Why it exists

CRUD layer for ``channel_wechat_credentials``. One row per agent. Holds
the iLink ``bot_token`` (base64-encoded at rest), the optional
per-account ``base_url``, the bot's own wxid, and the owner identity.

Mirrors Telegram's ``_telegram_credential_manager.py`` end-to-end. Two
deltas vs Telegram are captured here so the structural symmetry stays
load-bearing.

## Design decisions

- **``bind`` does NO network validation — it's a pure upsert.** Unlike
  Telegram (which calls ``getMe`` to validate the token at bind time),
  the iLink ``bot_token`` is **already proven** by the QR-scan confirm:
  the gateway only hands back a token after the owner physically scans
  the login QR with their phone. The bind route passes that
  post-confirm token straight in, so re-validating against the gateway
  would be redundant. ``bind`` therefore just upserts
  ``(bot_token, base_url, owner_user_id)`` and never aborts on a network
  error.
- **``owner_wx_id`` is claimed on the FIRST inbound DM, not at bind.**
  This is the **load-bearing trust-model gotcha.** The owner's WeChat id
  (``wxid``) is **opaque until they actually message the bound account**
  — the bind itself is owner-initiated from the Brain panel, but nothing
  in the bind payload reveals the owner's wxid. So ``claim_owner`` does a
  **compare-and-set on an empty ``owner_wx_id``**: the DB ``update``
  filters on ``owner_wx_id = ''`` and only succeeds if the slot is still
  empty. First DM wins. **Load-bearing coupling:** because the CAS keys
  on the empty *string*, ``owner_wx_id`` MUST be stored as ``''`` and
  never ``NULL`` — SQL ``= ''`` does not match ``NULL``, so a NULL slot
  would make the owner unclaimable forever. This is enforced on both
  sides: the schema column is ``NOT NULL DEFAULT ''`` and ``bind``'s
  insert sets ``owner_wx_id=""`` explicitly. A unit test
  (``test_claim_owner_is_first_dm_wins``) pins it. Telegram resolves its
  owner the same deferred
  way (``update_owner``) because ``getChat`` won't accept a user @handle;
  the WeChat reason is different (opaque wxid) but the mechanism is
  identical.
- **The CAS is "first-DM-wins" *because* a re-bind re-opens the claim.**
  Once ``owner_wx_id`` is set, every later DM's ``claim_owner`` fails the
  CAS (the filter no longer matches), so ownership is locked. The only
  way to re-open the claim is to **re-bind** (which, on a fresh insert,
  starts ``owner_wx_id`` empty again). This is the documented, intended
  trust model — NOT a bug. It is weaker than Telegram's
  username-as-lock (a stranger DM'ing the freshly-bound account before
  the real owner could claim ownership), accepted because the bind is
  owner-initiated and the window between bind and first owner DM is
  small and operator-controlled.
- **``owner_wx_id`` vs ``owner_user_id`` are two different identities.**
  ``owner_wx_id`` is the WeChat-side id claimed on first DM (used at
  runtime to decide "is the owner interacting?"). ``owner_user_id`` is
  the NarraNexus account (``agents.created_by``) supplied at bind. Don't
  conflate them.
- **Token base64-encoded at rest, decoded on load.** Same convention as
  Telegram / Slack / Lark. **Encoding is NOT encryption** — it is an
  honest placeholder that keeps a casual ``less`` / ``SELECT *`` from
  bleeding the token to a screenshot. At-rest encryption is out of scope
  (local SQLite or filesystem-protected MySQL). ``_encode_token`` /
  ``_decode_token`` are the only places that touch the wire format.
- **``to_public_dict`` strips ``bot_token``.** This is what the REST
  credential route returns. The token only ever lives in
  ``WeChatCredential.bot_token`` (in-memory, decoded) or
  ``bot_token_encoded`` (DB column). The dataclass field comment marks
  it "never log".
- **``set_enabled`` soft-disables without deleting.** The trigger flips
  this to stop reconnecting against a dead session — when ``getupdates``
  returns ``ret != 0`` (session expired), the trigger disables the
  credential rather than spinning forever. ``list_active`` filters on
  ``enabled = 1`` so a disabled credential drops out of the poll set on
  the next sweep.
- **``list_active`` filters on ``enabled = 1`` only.** Single condition;
  the trigger loads the active set from here.

## Upstream / downstream

- **Reads / writes**: ``channel_wechat_credentials`` table (registered
  in ``utils/schema_registry.py``).
- **No gateway calls.** Unlike the Telegram manager (which calls
  ``getMe`` / ``getChat`` / ``deleteWebhook``), this manager never
  touches the network — token validity is established by the QR-scan
  confirm upstream of ``bind``.
- **Used by**: the WeChat bind route (``bind``), the WeChat trigger
  (``list_active`` to load the poll set, ``claim_owner`` on first DM,
  ``set_enabled`` to disable a dead session), the WeChat module's
  ``get_credential`` path, the ``wechat_send`` MCP tool (to read the
  token + base_url), and the channel-cleanup registry on agent delete.

## Gotchas

- **Re-binding silently re-opens owner claim.** If an operator re-binds
  an agent, ``owner_wx_id`` resets to empty on a fresh insert and the
  next DM (from anyone) can re-claim ownership. This is intended but
  surprising — flag it if you change the bind upsert to preserve owner
  fields across re-bind.
- **``claim_owner`` returns False both when "already claimed" and when
  "no such row".** Callers can't distinguish "someone else already owns
  it" from "agent isn't bound". Fine today (the trigger only cares
  whether *it* claimed), but don't build authorization logic on the
  False alone.
- **Encoding ≠ encryption.** Anyone with DB read access can trivially
  base64-decode the token. If WeChat tokens ever become high-value,
  this needs real at-rest encryption — the placeholder is honest about
  not providing it.
- ``bind`` preserves an existing ``owner_user_id`` when the new bind
  passes an empty one (``owner_user_id or existing.owner_user_id``), but
  it does **not** preserve ``owner_wx_id`` on the update path because the
  update ``row`` simply omits that column — only a fresh insert clears
  it. Worth re-checking if you touch the bind row shape.
