"""
@file_name: steer_schema.py
@author: Bin Liang
@date: 2026-08-21
@description: Live-steering injection — one message routed INTO a turn
that is already running, instead of triggering a fresh turn.

One uniform inbox for the "append to the running loop" capability. Its
job is DECOUPLING, not durability: the running loop drains ONE store
instead of reaching into each producer's native home. That matters
because the producers are heterogeneous — a team room post already lives
in ``bus_messages``, but an owner-chat interjection is not a bus message
at all, so "just query bus_messages" would miss half the producers, and
every new source would add another special case in the feeder. Same
outbox/inbox pattern the codebase already uses for
``instance_artifact_events``. (IM producers are out of scope for v1.) A
producer writes one of these keyed by the target run; the transport drains
the run's unconsumed rows into its ``SteeringInlet`` at the next step
boundary.

NOTE (2026-08-24): the owner-chat producer's FIRST landing (the chat
WebSocket, PR #355) does NOT go through this inbox — it pushes the
interjection straight into the in-flight run's in-process ``SteerChannel``
(ephemeral, bounded on the WS write edge by the same MAX_CONTENT_BYTES /
MAX_UNCONSUMED_PER_RUN this repo defines). So for that path the
interjection's EFFECT persists (it rides the turn and shapes the assistant
reply, which chat memory does save), but the literal owner message is not
itself written to chat memory yet — persisting it on consumption (for
refresh-history fidelity + next-turn recall) is a scoped follow-up, not the
current behaviour. Do not read this inbox as the owner-chat path of record
until that lands.

``consumed_at`` is the OTHER thing the bus cannot lend: a per-RUN consume
cursor. The bus's ``last_processed_at`` is per ``(agent, channel)`` and
is already the trigger's (it decides new dispatches); it cannot double as
a running turn's steer cursor — least of all when one agent has several
concurrent runs. Per row, per run, is the only shape that survives that.

``run_id`` is deliberately OPAQUE here: this schema does not know how the
orchestrator identifies a live run (that is the RunRegistry's concern),
only that whoever produces and whoever drains agree on the handle. That
keeps the storage layer decoupled from the routing design.

``source`` records which producer wrote it so the prompt layer can phrase
the injection appropriately (a teammate's room message reads differently
from the owner interjecting) — the mechanism is identical, the wording is
not. Injection is append-only by contract; nothing here mutates a prior
row's content.
"""

from datetime import datetime
from typing import Literal, Optional

#: Key under which a steering injection carries its id inside a provider
#: message until consumed (the inlet strips it on drain — the model never
#: sees it). Shared by the producer (SteerChannel) and the consumer (the
#: framework's inlet / subprocess transport) — declared on the platform schema
#: so neither side reaches into the other's package for it.
STEER_ID_KEY = "_steer_id"

from pydantic import BaseModel

#: Which producer wrote an injection. A closed set, not a free string: the
#: prompt layer branches on it (a teammate's room message reads differently
#: from the owner interjecting), so a typo must fail at the boundary, not fall
#: through to a wrong-wording fallback. Module-level Literal per the schema/
#: convention (ArtifactKind, EmbedMode, ExecutorEventType). Extend the set here
#: when a real new producer ships.
SteerSource = Literal["team", "owner_chat"]


class SteerInjection(BaseModel):
    """One message to fold into a running turn's context.

    Store-assigned fields — a producer does not set these; whatever it passes
    is overwritten:
    * ``id`` — the arrival order and the consume cursor's unit; ``None`` until
      persisted.
    * ``created_at`` — the DB default stamps it (so it is never the producer's
      idea of "when the source sent it"; if that ever matters, add a distinct
      column rather than repurposing this one).
    * ``consumed_at`` — ``None`` while pending; stamped when a run has drained
      the row. Under one drainer per run (the design) that gives at-most-once
      injection; it is NOT a lock, so concurrent drainers of one run are not
      guarded by it (see the repository's delivery-semantics note).
    """

    id: Optional[int] = None
    run_id: str
    msg_id: str
    role: str = "user"
    content: str
    sender_id: str
    source: SteerSource
    created_at: Optional[datetime] = None
    consumed_at: Optional[datetime] = None
